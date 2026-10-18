use std::{env, error::Error, time::Duration};

use ec00_oa::{
    apis::{configuration::Configuration, default_api::{publish, revise}, Error as ApiError},
    models::{PublishRequest, ReviseRequest},
};
use serde::Serialize;
use tonic::{metadata::MetadataValue, Code, Request};

pub mod oa {
    tonic::include_proto!("ec00.oa.v1");
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Output {
    language: &'static str,
    proto_id: String,
    proto_revision: i64,
    proto_null_note_id: String,
    proto_absent_note_id: String,
    proto_absent: String,
    proto_zero: String,
    http_id: String,
    http_revision: i64,
    http_null_note_id: String,
    http_absent_note_id: String,
    http_absent: u16,
    http_null: u16,
    http_zero: u16,
}

fn api_config(base: &str, key: &str) -> Result<Configuration, Box<dyn Error>> {
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert("Idempotency-Key", reqwest::header::HeaderValue::from_str(key)?);
    let mut configuration = Configuration::new();
    configuration.base_path = base.to_owned();
    configuration.client = reqwest::Client::builder()
        .default_headers(headers)
        .timeout(Duration::from_secs(10))
        .build()?;
    Ok(configuration)
}

fn response_status<T>(error: ApiError<T>) -> Result<u16, Box<dyn Error>> {
    match error {
        ApiError::ResponseError(response) => Ok(response.status.as_u16()),
        other => Err(format!("unexpected generated OpenAPI client error: {other}").into()),
    }
}

fn grpc_request<T>(value: T, idempotency_key: &'static str) -> Result<Request<T>, Box<dyn Error>> {
    let mut request = Request::new(value);
    request.metadata_mut().insert("idempotency-key", MetadataValue::try_from(idempotency_key)?);
    request.set_timeout(Duration::from_secs(10));
    Ok(request)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let grpc_target = env::var("EC00_GRPC_TARGET")?;
    let base = env::var("EC00_BASE_URL")?;
    let channel = tonic::transport::Endpoint::from_shared(format!("http://{grpc_target}"))?
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(10))
        .connect().await?;
    let mut client = oa::owner_operations_client::OwnerOperationsClient::new(channel);

    let proto_id = "9007199254741021".to_owned();
    let proto_publish = grpc_request(oa::PublishRequest {
        request_id: proto_id.clone(),
        objective: Some("Rust native gRPC publish".to_owned()),
        note_value: Some(oa::publish_request::NoteValue::NoteNull(0)),
        ..Default::default()
    }, "rust-grpc-publish")?;
    let published = client.publish(proto_publish).await?.into_inner();
    if published.request_id != proto_id || published.revision != 1 {
        return Err(format!("protobuf publish result mismatch: {published:?}").into());
    }
    let proto_null_note_id = "9007199254741041".to_owned();
    let null_note = grpc_request(oa::PublishRequest {
        request_id: proto_null_note_id.clone(),
        objective: Some("Rust native gRPC null note".into()),
        note_value: Some(oa::publish_request::NoteValue::NoteNull(0)),
        ..Default::default()
    }, "rust-grpc-publish-null-note")?;
    client.publish(null_note).await?;
    let proto_absent_note_id = "9007199254741043".to_owned();
    let absent_note = grpc_request(oa::PublishRequest {
        request_id: proto_absent_note_id.clone(),
        objective: Some("Rust native gRPC absent note".into()),
        note_value: None,
        ..Default::default()
    }, "rust-grpc-publish-absent-note")?;
    client.publish(absent_note).await?;

    let absent = grpc_request(oa::ReviseRequest {
        request_id: proto_id.clone(), objective: Some("absent".into()),
        expected_revision: None, note_value: None,
    }, "rust-grpc-revise-absent")?;
    let proto_absent_code = match client.revise(absent).await {
        Err(status) => status.code(),
        Ok(_) => return Err("protobuf absent expected_revision unexpectedly accepted".into()),
    };
    if proto_absent_code != Code::InvalidArgument { return Err(format!("unexpected absent CAS status: {proto_absent_code:?}").into()); }

    let zero = grpc_request(oa::ReviseRequest {
        request_id: proto_id.clone(), objective: Some("zero".into()),
        expected_revision: Some(0), note_value: None,
    }, "rust-grpc-revise-zero")?;
    let proto_zero_code = match client.revise(zero).await {
        Err(status) => status.code(),
        Ok(_) => return Err("protobuf stale CAS unexpectedly accepted".into()),
    };
    if proto_zero_code != Code::Aborted { return Err(format!("unexpected stale CAS status: {proto_zero_code:?}").into()); }

    let valid = grpc_request(oa::ReviseRequest {
        request_id: proto_id.clone(), objective: Some("Rust native gRPC revision".into()),
        expected_revision: Some(1),
        note_value: Some(oa::revise_request::NoteValue::Note(String::new())),
    }, "rust-grpc-revise-valid")?;
    let revised = client.revise(valid).await?.into_inner();
    if revised.revision != 2 { return Err(format!("protobuf revision mismatch: {revised:?}").into()); }

    let http_id = "9007199254741023".to_owned();
    let mut publish_body = PublishRequest::new(http_id.clone(), "Rust OpenAPI publish".into());
    publish_body.note = Some(None);
    let published_http = publish(&api_config(&base, "rust-openapi-publish")?, publish_body).await?;
    if published_http.request_id != http_id || published_http.revision != 1 {
        return Err(format!("OpenAPI publish result mismatch: {published_http:?}").into());
    }

    let http_absent_code = match revise(&api_config(&base, "rust-openapi-revise-absent")?, &http_id,
        ReviseRequest::new(http_id.clone(), "absent".into())).await {
        Err(error) => response_status(error)?,
        Ok(_) => return Err("OpenAPI absent expectedRevision unexpectedly accepted".into()),
    };
    let mut null_body = ReviseRequest::new(http_id.clone(), "null".into());
    null_body.expected_revision = Some(None);
    let http_null_code = match revise(&api_config(&base, "rust-openapi-revise-null")?, &http_id, null_body).await {
        Err(error) => response_status(error)?,
        Ok(_) => return Err("OpenAPI null expectedRevision unexpectedly accepted".into()),
    };
    let mut zero_body = ReviseRequest::new(http_id.clone(), "zero".into());
    zero_body.expected_revision = Some(Some(0));
    let http_zero_code = match revise(&api_config(&base, "rust-openapi-revise-zero")?, &http_id, zero_body).await {
        Err(error) => response_status(error)?,
        Ok(_) => return Err("OpenAPI stale expectedRevision unexpectedly accepted".into()),
    };
    let mut valid_body = ReviseRequest::new(http_id.clone(), "Rust OpenAPI revision".into());
    valid_body.expected_revision = Some(Some(1));
    valid_body.note = Some(Some(String::new()));
    let updated = revise(&api_config(&base, "rust-openapi-revise-valid")?, &http_id, valid_body).await?;
    if updated.revision != 2 { return Err(format!("OpenAPI revision mismatch: {updated:?}").into()); }

    let http_null_note_id = "9007199254741045".to_owned();
    let mut null_note_body = PublishRequest::new(http_null_note_id.clone(), "Rust OpenAPI null note".into());
    null_note_body.note = Some(None);
    publish(&api_config(&base, "rust-openapi-publish-null-note")?, null_note_body).await?;
    let http_absent_note_id = "9007199254741047".to_owned();
    let absent_note_body = PublishRequest::new(http_absent_note_id.clone(), "Rust OpenAPI absent note".into());
    publish(&api_config(&base, "rust-openapi-publish-absent-note")?, absent_note_body).await?;
    if (http_absent_code, http_null_code, http_zero_code) != (422, 422, 409) {
        return Err(format!("unexpected OpenAPI refusal codes: {http_absent_code}, {http_null_code}, {http_zero_code}").into());
    }

    let output = Output {
        language: "rust", proto_id, proto_revision: revised.revision,
        proto_null_note_id, proto_absent_note_id,
        proto_absent: format!("{proto_absent_code:?}"), proto_zero: format!("{proto_zero_code:?}"),
        http_id, http_revision: updated.revision, http_null_note_id, http_absent_note_id,
        http_absent: http_absent_code,
        http_null: http_null_code, http_zero: http_zero_code,
    };
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}
