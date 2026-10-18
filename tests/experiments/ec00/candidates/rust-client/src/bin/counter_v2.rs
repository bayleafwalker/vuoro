use std::{env, error::Error, io, time::Duration};

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

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let base_url = env::var("EC00_BASE_URL")?;
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
