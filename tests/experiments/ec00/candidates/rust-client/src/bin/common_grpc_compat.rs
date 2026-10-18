use std::{env, error::Error, time::Duration};

use serde::Serialize;
use tonic::{metadata::MetadataValue, transport::Endpoint, Code, Request};

pub mod oa {
    tonic::include_proto!("ec00.oa.v1");
}

#[derive(Serialize)]
struct ResultRow {
    language: String,
    request_ids: Vec<String>,
    revisions: Vec<i64>,
}

fn request<T>(value: T, key: &str) -> Result<Request<T>, Box<dyn Error>> {
    let mut request = Request::new(value);
    request.metadata_mut().insert("idempotency-key", MetadataValue::try_from(key)?);
    request.set_timeout(Duration::from_secs(5));
    Ok(request)
}

fn expect_code<T>(result: Result<T, tonic::Status>, expected: Code) -> Result<(), Box<dyn Error>> {
    match result {
        Err(status) if status.code() == expected => Ok(()),
        Err(status) => Err(format!("expected {expected:?}, got {:?}: {}", status.code(), status.message()).into()),
        Ok(_) => Err(format!("expected {expected:?}, call succeeded").into()),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let target = env::var("EC00_GRPC_TARGET")?;
    let base: i64 = env::var("EC00_COMPAT_ID_BASE")?.parse()?;
    let language = env::var("EC00_COMPAT_LANGUAGE")?;
    let display_language = match language.as_str() {
        "go" => "Go",
        "rust" => "Rust",
        _ => return Err("EC00_COMPAT_LANGUAGE must be go or rust".into()),
    };
    let endpoint = Endpoint::from_shared(format!("http://{target}"))?
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(5));
    let channel = endpoint.connect().await?;
    let mut client = oa::owner_operations_client::OwnerOperationsClient::new(channel);
    let id = base.to_string();
    let published = oa::PublishRequest {
        request_id: id.clone(), objective: Some(format!("{display_language} current common publish")), note_value: None,
        ..Default::default()
    };
    let publish_key = format!("current-common-publish-{id}");
    let first = client.publish(request(published.clone(), &publish_key)?).await?.into_inner();
    let replay = client.publish(request(published.clone(), &publish_key)?).await?.into_inner();
    if first.revision != 1 || replay.revision != 1 { return Err("publish replay revision mismatch".into()); }
    expect_code(client.publish(request(oa::PublishRequest {
        request_id: id.clone(), objective: Some("different body".into()), note_value: None,
        ..Default::default()
    }, &publish_key)?).await.map(|r| r.into_inner()), Code::Aborted)?;

    expect_code(client.revise(request(oa::ReviseRequest {
        request_id: id.clone(), expected_revision: None, objective: Some("missing CAS".into()), note_value: None,
    }, &format!("missing-{id}"))?).await.map(|r| r.into_inner()), Code::InvalidArgument)?;
    expect_code(client.revise(request(oa::ReviseRequest {
        request_id: id.clone(), expected_revision: Some(0), objective: Some("zero CAS".into()), note_value: None,
    }, &format!("zero-{id}"))?).await.map(|r| r.into_inner()), Code::Aborted)?;

    let revised_body = oa::ReviseRequest {
        request_id: id.clone(), expected_revision: Some(1),
        objective: Some(format!("{display_language} current common revise")),
        note_value: Some(oa::revise_request::NoteValue::Note(String::new())),
    };
    let revise_key = format!("current-common-revise-{id}");
    let updated = client.revise(request(revised_body.clone(), &revise_key)?).await?.into_inner();
    let revise_replay = client.revise(request(revised_body, &revise_key)?).await?.into_inner();
    if updated.revision != 2 || revise_replay.revision != 2 { return Err("revise replay revision mismatch".into()); }
    expect_code(client.revise(request(oa::ReviseRequest {
        request_id: id.clone(), expected_revision: Some(1), objective: Some("stale".into()), note_value: None,
    }, &format!("stale-{id}"))?).await.map(|r| r.into_inner()), Code::Aborted)?;

    let mut ids = vec![id];
    for (offset, kind) in ["absent", "empty", "null"].iter().enumerate() {
        let request_id = (base + ((offset + 1) * 2) as i64).to_string();
        let note_value = match *kind {
            "absent" => None,
            "empty" => Some(oa::publish_request::NoteValue::Note(String::new())),
            _ => Some(oa::publish_request::NoteValue::NoteNull(0)),
        };
        let created = client.publish(request(oa::PublishRequest {
            request_id: request_id.clone(), objective: Some(format!("note presence {kind}")), note_value,
            ..Default::default()
        }, &format!("presence-{request_id}"))?).await?.into_inner();
        if created.revision != 1 { return Err(format!("{kind} note publish revision mismatch").into()); }
        ids.push(request_id);
    }

    println!("{}", serde_json::to_string(&ResultRow {
        language,
        request_ids: ids, revisions: vec![first.revision, updated.revision],
    })?);
    Ok(())
}
