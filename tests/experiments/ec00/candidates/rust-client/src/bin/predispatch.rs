//! Poll the actual generated SDK into reqwest DNS before cancelling its wait.
//! The resolver is a fixture barrier; it never resolves an external host.
use std::{
    env,
    error::Error,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};

use ec00_oa_v2::{apis::{configuration::Configuration, default_api}, models};
use reqwest::{dns::{Addrs, Name, Resolve, Resolving}, header::{HeaderMap, HeaderValue}};
use tokio::sync::Notify;

#[derive(Default)]
struct Gate {
    entered: Notify,
    finished: Notify,
    release: Notify,
    opened: AtomicBool,
    resolutions: AtomicUsize,
    dropped: AtomicUsize,
}

struct ResolutionLifetime(Arc<Gate>);

impl Drop for ResolutionLifetime {
    fn drop(&mut self) {
        self.0.dropped.fetch_add(1, Ordering::SeqCst);
        self.0.finished.notify_one();
    }
}

struct Resolver(Arc<Gate>);

impl Resolve for Resolver {
    fn resolve(&self, name: Name) -> Resolving {
        let gate = Arc::clone(&self.0);
        Box::pin(async move {
            assert_eq!(name.as_str(), "ec00-predispatch.invalid");
            let _lifetime = ResolutionLifetime(Arc::clone(&gate));
            gate.resolutions.fetch_add(1, Ordering::SeqCst);
            gate.entered.notify_one();
            while !gate.opened.load(Ordering::SeqCst) {
                gate.release.notified().await;
            }
            let addresses: Addrs = Box::new(
                vec![SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 0)].into_iter(),
            );
            Ok(addresses)
        })
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let mode = env::var("EC00_PREDISPATCH_MODE")?;
    if !["cancel", "deadline", "control", "unpolled"].contains(&mode.as_str()) {
        return Err("unknown fixture mode".into());
    }
    let port: u16 = env::var("EC00_PREDISPATCH_PORT")?.parse()?;
    let gate = Arc::new(Gate::default());
    let mut headers = HeaderMap::new();
    headers.insert("idempotency-key", HeaderValue::from_static("rust-polled-predispatch"));
    let mut configuration = Configuration::new();
    configuration.base_path = format!("http://ec00-predispatch.invalid:{port}");
    configuration.client = reqwest::Client::builder()
        .no_proxy()
        .default_headers(headers)
        .dns_resolver(Arc::new(Resolver(Arc::clone(&gate))))
        .timeout(Duration::from_secs(5))
        .build()?;
    let request_id = "9007199254741199";
    let body = models::ReviseRequest {
        request_id: request_id.into(),
        expected_revision: Some(Some("1".into())),
        objective: "polled Rust SDK fixture".into(),
        note: None,
    };
    let mut call = Box::pin(default_api::revise(&configuration, request_id, body));
    if mode == "unpolled" {
        drop(call);
        println!("{}", serde_json::json!({
            "language":"rust", "mode":mode, "resolverEntered":gate.resolutions.load(Ordering::SeqCst),
            "resolverFinished":gate.dropped.load(Ordering::SeqCst), "revision":null
        }));
        return Ok(());
    }
    // A no-op or unpolled future cannot pass this observed transport boundary.
    tokio::select! {
        result = &mut call => return Err(format!("request escaped DNS barrier: {result:?}").into()),
        result = tokio::time::timeout(Duration::from_secs(2), gate.entered.notified()) => result?,
    }
    let mut revision = None;
    if mode == "control" {
        gate.opened.store(true, Ordering::SeqCst);
        gate.release.notify_one();
        revision = Some(call.await?.revision);
    } else if mode == "cancel" {
        drop(call);
    } else if tokio::time::timeout(Duration::ZERO, call).await.is_ok() {
        return Err("expired await budget unexpectedly completed".into());
    }
    // Release even after cancellation so a background resolver cannot hide a
    // delayed owner invocation behind an intentionally blocked DNS future.
    gate.opened.store(true, Ordering::SeqCst);
    gate.release.notify_one();
    tokio::time::timeout(Duration::from_secs(2), gate.finished.notified()).await?;
    println!("{}", serde_json::json!({
        "language":"rust", "mode":mode, "resolverEntered":gate.resolutions.load(Ordering::SeqCst),
        "resolverFinished":gate.dropped.load(Ordering::SeqCst), "revision":revision,
        "scope":"generated SDK and reqwest entered before owner dispatch; caller cancellation is not owner rollback"
    }));
    Ok(())
}
