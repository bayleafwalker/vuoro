use std::{env, error::Error, io::{self, Read}, time::Duration};

use ec00_oa_v2::{
    apis::{configuration::Configuration, default_api, Error as ApiError},
    models,
};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use serde::Serialize;

const REQUEST_ID: &str = "9007199254741199";
const IDEMPOTENCY_KEY: &str = "v2-cas";
const CALL_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Serialize)]
struct ResultRow {
    language: &'static str,
    #[serde(rename = "requestId")]
    request_id: String,
    revision: String,
    #[serde(rename = "replayRevision")]
    replay_revision: String,
    #[serde(rename = "changedStatus")]
    changed_status: u16,
}

async fn revise(
    configuration: &Configuration,
    request: models::ReviseRequest,
) -> Result<Result<models::MutationResult, ApiError<default_api::ReviseError>>, Box<dyn Error>> {
    match tokio::time::timeout(
        CALL_TIMEOUT,
        default_api::revise(configuration, REQUEST_ID, request),
    )
    .await
    {
        Ok(result) => Ok(result),
        Err(_) => Err(io::Error::new(io::ErrorKind::TimedOut, "v2 revise call exceeded 5 seconds").into()),
    }
}

fn request(expected_revision: &str, objective: &str) -> models::ReviseRequest {
    models::ReviseRequest {
        request_id: REQUEST_ID.to_owned(),
        expected_revision: Some(Some(expected_revision.to_owned())),
        objective: objective.to_owned(),
        note: None,
    }
}

// Keep the actual generated decoder behavior, including its refusals.
async fn observe_unknown_values(base_url: String) -> Result<(), Box<dyn Error>> {
    fn observe<T: Serialize, E>(result: Result<T, E>) -> serde_json::Value {
        match result {
            Ok(value) => serde_json::json!({"decoded": true, "value": value}),
            Err(_) => serde_json::json!({"decoded": false, "error": "generated API/decoder refusal"}),
        }
    }
    let mut configuration = Configuration::new();
    configuration.base_path = base_url.trim_end_matches('/').to_owned();
    configuration.client = reqwest::Client::builder().timeout(CALL_TIMEOUT).build()?;
    let request = default_api::read_request(&configuration, REQUEST_ID).await;
    let artifact = default_api::read_artifact(&configuration, REQUEST_ID, "1").await;
    println!("{}", serde_json::json!({
        "language": "rust", "request": observe(request), "artifact": observe(artifact)
    }));
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let base_url = env::var("EC00_BASE_URL")?;
    if let Ok(mode) = env::var("EC00_NATIVE_MODE") {
        return native_mode(base_url, mode).await;
    }
    if env::var("EC00_UNKNOWN_VALUES").as_deref() == Ok("1") {
        if base_url.is_empty() {
            return Err(io::Error::other("EC00_BASE_URL is required").into());
        }
        return observe_unknown_values(base_url).await;
    }
    let expected_revision = env::var("EC00_EXPECTED_REVISION")?;
    if base_url.is_empty() || expected_revision.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "EC00_BASE_URL and EC00_EXPECTED_REVISION are required",
        )
        .into());
    }

    let mut headers = HeaderMap::new();
    headers.insert(
        HeaderName::from_static("idempotency-key"),
        HeaderValue::from_static(IDEMPOTENCY_KEY),
    );
    let client = reqwest::Client::builder()
        .default_headers(headers)
        .timeout(CALL_TIMEOUT)
        .build()?;
    let mut configuration = Configuration::new();
    configuration.base_path = base_url.trim_end_matches('/').to_owned();
    configuration.client = client;

    let first = revise(&configuration, request(&expected_revision, "versioned counter effect"))
        .await??;
    if first.request_id != REQUEST_ID {
        return Err(io::Error::other(format!(
            "first revise returned request ID {:?}, expected {:?}",
            first.request_id, REQUEST_ID
        ))
        .into());
    }
    let replay = revise(&configuration, request(&expected_revision, "versioned counter effect"))
        .await??;
    if replay.request_id != REQUEST_ID {
        return Err(io::Error::other(format!(
            "same-key replay returned request ID {:?}, expected {:?}",
            replay.request_id, REQUEST_ID
        ))
        .into());
    }

    let changed = revise(&configuration, request(&expected_revision, "changed versioned effect"))
        .await?;
    let changed_status = match changed {
        Err(ApiError::ResponseError(response)) if response.status.as_u16() == 409 => {
            response.status.as_u16()
        }
        Err(error) => {
            return Err(io::Error::other(format!(
                "same-key changed-body request expected HTTP 409: {error}"
            ))
            .into());
        }
        Ok(_) => {
            return Err(io::Error::other("same-key changed-body request unexpectedly succeeded").into());
        }
    };

    println!(
        "{}",
        serde_json::to_string(&ResultRow {
            language: "rust",
            request_id: first.request_id,
            revision: first.revision,
            replay_revision: replay.revision,
            changed_status,
        })?
    );
    Ok(())
}

// Native generated reqwest calls are futures. Dropping the in-flight future is
// cancellation of the wait; it cannot roll back an owner transaction.
async fn native_mode(base_url: String, mode: String) -> Result<(), Box<dyn Error>> {
    let state: serde_json::Value = serde_json::from_slice(&std::fs::read(env::var("EC00_NATIVE_STATE")?)?)?;
    let mut headers = HeaderMap::new();
    headers.insert(HeaderName::from_static("idempotency-key"), HeaderValue::from_str(state["key"].as_str().ok_or("missing key")?)?);
    let mut configuration = Configuration::new();
    configuration.base_path = base_url.trim_end_matches('/').to_owned();
    configuration.client = reqwest::Client::builder().default_headers(headers).timeout(CALL_TIMEOUT).build()?;
    fn observation<T: Serialize, E: std::fmt::Debug>(value: Result<T, ApiError<E>>) -> serde_json::Value {
        match value {
            Ok(value) => serde_json::json!({"status":200,"value":value}),
            Err(ApiError::ResponseError(response)) => serde_json::json!({"status":response.status.as_u16(),"error":"HTTP refusal"}),
            Err(error) => serde_json::json!({"status":0,"error":format!("{error:?}")}),
        }
    }
    let request_id = state["requestId"].as_str().ok_or("missing requestId")?;
    let body = models::ReviseRequest {
        request_id: request_id.to_owned(),
        expected_revision: Some(Some(state["expectedRevision"].as_str().ok_or("missing revision")?.to_owned())),
        objective: state["objective"].as_str().ok_or("missing objective")?.to_owned(), note: None,
    };
    let mutation = default_api::revise(&configuration, request_id, body.clone());
    let read = default_api::watch(&configuration, state["cursor"].as_str(), Some(2));
    let call = async {
        if mode == "watch" {
            let row = observation(read.await);
            serde_json::json!({"status":row["status"],"page":row["value"],"error":row["error"]})
        } else {
            let row = observation(mutation.await);
            if mode == "reconcile" {
                let replay = observation(default_api::revise(&configuration, request_id, body.clone()).await);
                let changed = observation(default_api::revise(&configuration, request_id,
                    models::ReviseRequest {objective:"changed native effect".to_owned(), ..body}).await);
                serde_json::json!({"status":row["status"],"first":row["value"],"replay":replay["value"],
                    "replayStatus":replay["status"],"changedStatus":changed["status"],"error":row["error"]})
            } else { serde_json::json!({"status":row["status"],"first":row["value"],"error":row["error"]}) }
        }
    };
    let failure = env::var("EC00_NATIVE_FAILURE").unwrap_or_default();
    let predispatch = env::var("EC00_NATIVE_PREDISPATCH").as_deref() == Ok("1");
    let mut output = if failure == "cancel" && predispatch {
        drop(call); // The generated future has never been polled: no dispatch.
        serde_json::json!({"status":0,"error":"cancelled-unpolled-future"})
    } else if failure == "cancel" {
        let cancellation = tokio::task::spawn_blocking(|| {
            let _ = io::stdin().read_exact(&mut [0u8; 1]);
        });
        tokio::select! {
            result = call => result,
            _ = cancellation => serde_json::json!({"status":0,"error":"cancelled-dropped-future"}),
        }
    } else if failure == "deadline" {
        let deadline = if predispatch { tokio::time::Instant::now() - Duration::from_secs(1) }
            else { tokio::time::Instant::now() + Duration::from_secs(1) };
        match tokio::time::timeout_at(deadline, call).await {
            Ok(result) => result,
            Err(_) => serde_json::json!({"status":0,"error":"deadline-elapsed-dropped-future"}),
        }
    } else { call.await };
    output["language"] = serde_json::json!("rust");
    println!("{output}");
    Ok(())
}
