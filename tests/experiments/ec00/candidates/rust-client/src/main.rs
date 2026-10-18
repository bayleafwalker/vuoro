use std::{collections::HashMap, env, error::Error, time::Duration};
use base64::{engine::general_purpose::STANDARD, Engine as _};

use ec00_oa::{
    apis::{configuration::Configuration, default_api::{acknowledge_continuation, decide_artifact, list_requests, publish, read_artifact, read_dependants, read_request, report_performed, resolve, revise, verify, watch, withdraw_dependent}, Error as ApiError},
    models::{AcknowledgeRequest, DecideArtifactRequest, PublishRequest, ReviseRequest, RevisionRequest, VerifyRequest, WithdrawRequest},
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

fn grpc_request<T>(value: T, idempotency_key: &str) -> Result<Request<T>, Box<dyn Error>> {
    let mut request = Request::new(value);
    request.metadata_mut().insert("idempotency-key", MetadataValue::try_from(idempotency_key)?);
    request.set_timeout(Duration::from_secs(10));
    Ok(request)
}

#[derive(serde::Deserialize, Clone)]
struct Endpoint { http: String, grpc: String }
#[derive(serde::Deserialize, Clone)]
struct Artifact { request_id: String, objective: String, artifact_digest: String, key_id: String,
    original_bytes: String, envelope_bytes: String, signature: String,
    #[serde(default)] revision: String, #[serde(default)] material_revision: String, #[serde(default)] sequence: String }

fn load_fixtures() -> Result<(HashMap<String,Endpoint>,HashMap<String,Artifact>),Box<dyn Error>> {
    Ok((serde_json::from_str(&env::var("EC00_ENDPOINTS")?)?,serde_json::from_str(&env::var("EC00_ARTIFACTS")?)?))
}
fn api_for(eps:&HashMap<String,Endpoint>,role:&str,key:&str)->Result<Configuration,Box<dyn Error>> { Ok(api_config(&eps[role].http,key)?) }
async fn http_full(eps:&HashMap<String,Endpoint>,a:&Artifact,prefix:&str)->Result<(),Box<dyn Error>> {
    let mut body=PublishRequest::new(a.request_id.clone(),a.objective.clone()); body.dependants=Some(vec!["work-a".into(),"work-b".into(),"work-c".into()]);
    body.artifact_digest=Some(a.artifact_digest.clone()); body.artifact_key_id=Some(a.key_id.clone()); body.artifact_bytes=Some(STANDARD.decode(&a.original_bytes)?);
    body.artifact_envelope=Some(STANDARD.decode(&a.envelope_bytes)?); body.artifact_signature=Some(STANDARD.decode(&a.signature)?);
    let key=format!("{prefix}-full-publish");
    if publish(&api_for(eps,"coordinator-a",&key)?,body.clone()).await.is_ok(){return Err("post-commit HTTP publish fault was not observed".into())}
    publish(&api_for(eps,"coordinator-a",&key)?,body).await?;
    for (i,(role,op)) in [("requester-a2","withdraw"),("requester-a","report"),("verifier-a","verify"),("operator-a","decide"),("verifier-a","resolve"),("requester-a","ack-a"),("requester-a2","ack-b")].into_iter().enumerate(){
        let expected=(i+1) as i64; let key=format!("{prefix}-full-{op}"); let cfg=api_for(eps,role,&key)?;
        match op {
            "withdraw"=>{withdraw_dependent(&cfg,&a.request_id,WithdrawRequest::new("work-c".into(),expected)).await?;},
            "report"=>{report_performed(&cfg,&a.request_id,RevisionRequest::new(expected)).await?;},
            "verify"=>{verify(&cfg,&a.request_id,VerifyRequest::new(expected,format!("evidence:source:{}",a.request_id))).await?;},
            "decide"=>{decide_artifact(&cfg,&a.request_id,DecideArtifactRequest::new(expected,a.artifact_digest.clone())).await?;},
            "resolve"=>{resolve(&cfg,&a.request_id,RevisionRequest::new(expected)).await?;},
            "ack-a"=>{acknowledge_continuation(&cfg,&a.request_id,AcknowledgeRequest::new("work-a".into(),expected)).await?;},
            _=>{acknowledge_continuation(&cfg,&a.request_id,AcknowledgeRequest::new("work-b".into(),expected)).await?;},
        }
    }
    let cfg=api_for(eps,"verifier-a",&format!("{prefix}-read"))?;
    let req=read_request(&cfg,&a.request_id).await?; let deps=read_dependants(&cfg,&a.request_id).await?; let art=read_artifact(&cfg,&a.request_id,"1").await?;
    if req.revision!="8"||req.material_revision!="1"||req.created_seq.is_empty()||req.state!=ec00_oa::models::RequestState::Resolved||deps.items.len()!=3||art.artifact_digest!=a.artifact_digest||art.original_bytes!=STANDARD.decode(&a.original_bytes)?||art.envelope_bytes!=STANDARD.decode(&a.envelope_bytes)?||art.signature!=STANDARD.decode(&a.signature)?{return Err(format!("typed Rust HTTP read mismatch req={req:?} deps={deps:?} art={art:?}").into())}
    let page=list_requests(&cfg,None,Some(2)).await?; let events=watch(&cfg,None,Some(2)).await?;
    if page.items.is_empty()||events.events.is_empty(){return Err("typed Rust list/watch pages were empty".into())}
    Ok(())
}

async fn grpc_connect(target:&str)->Result<oa::owner_operations_client::OwnerOperationsClient<tonic::transport::Channel>,Box<dyn Error>>{
    let channel=tonic::transport::Endpoint::from_shared(format!("http://{target}"))?.connect_timeout(Duration::from_secs(10)).timeout(Duration::from_secs(10)).connect().await?;
    Ok(oa::owner_operations_client::OwnerOperationsClient::new(channel))
}

async fn big_read(eps:&HashMap<String,Endpoint>,a:&Artifact)->Result<(),Box<dyn Error>>{
    let cfg=api_for(eps,"verifier-a","rust-big-read")?;
    let r=read_request(&cfg,&a.request_id).await?;let art=read_artifact(&cfg,&a.request_id,&a.material_revision).await?;
    if r.revision!=a.revision||r.material_revision!=a.material_revision||r.created_seq!=a.sequence||art.artifact_digest!=a.artifact_digest||art.key_id!=a.key_id||!art.original_bytes.is_empty()||art.envelope_bytes!=STANDARD.decode(&a.envelope_bytes)?||art.signature!=STANDARD.decode(&a.signature)?{return Err(format!("Rust HTTP exact int64 read mismatch {r:?} {art:?}").into())}
    let mut c=grpc_connect(&eps["verifier-a"].grpc).await?;
    let pr=c.read_request(grpc_request(oa::RequestById{request_id:a.request_id.clone()},"rust-big-read-proto")?).await?.into_inner();
    let pa=c.read_artifact(grpc_request(oa::ReadArtifactRequest{request_id:a.request_id.clone(),material_revision:a.material_revision.parse::<i64>()?},"rust-big-artifact-proto")?).await?.into_inner();
    if pr.revision.to_string()!=a.revision||pr.material_revision.to_string()!=a.material_revision||pr.created_seq.to_string()!=a.sequence||pa.artifact_digest!=a.artifact_digest||pa.key_id!=a.key_id||!pa.original_bytes.is_empty()||pa.envelope_bytes!=STANDARD.decode(&a.envelope_bytes)?||pa.signature!=STANDARD.decode(&a.signature)?{return Err(format!("Rust protobuf exact int64 read mismatch {pr:?}").into())}
    Ok(())
}

async fn grpc_full(eps:&HashMap<String,Endpoint>,a:&Artifact,prefix:&str)->Result<(),Box<dyn Error>>{
    let original=STANDARD.decode(&a.original_bytes)?; let envelope=STANDARD.decode(&a.envelope_bytes)?; let signature=STANDARD.decode(&a.signature)?;
    let req=oa::PublishRequest{request_id:a.request_id.clone(),objective:Some(a.objective.clone()),dependent_ids:vec!["work-a".into(),"work-b".into(),"work-c".into()],artifact_digest:Some(a.artifact_digest.clone()),artifact_key_id:Some(a.key_id.clone()),artifact_bytes:Some(original.clone()),artifact_envelope:Some(envelope.clone()),artifact_signature:Some(signature.clone()),..Default::default()};
    let key=format!("{prefix}-full-publish");
    let mut client=grpc_connect(&eps["coordinator-a"].grpc).await?;
    if client.publish(grpc_request(req.clone(),&key)?).await.is_ok(){return Err("post-commit gRPC publish fault was not observed".into())}
    client=grpc_connect(&eps["coordinator-a"].grpc).await?; client.publish(grpc_request(req.clone(),&key)?).await?;
    for (i,(role,op)) in [("requester-a2","withdraw"),("requester-a","report"),("verifier-a","verify"),("operator-a","decide"),("verifier-a","resolve"),("requester-a","ack-a"),("requester-a2","ack-b")].into_iter().enumerate(){
        let expected=(i+1) as i64; let key=format!("{prefix}-full-{op}"); let mut c=grpc_connect(&eps[role].grpc).await?;
        match op {
            "withdraw"=>{c.withdraw_dependent(grpc_request(oa::WithdrawDependentRequest{request_id:a.request_id.clone(),dependent_id:"work-c".into(),expected_revision:Some(expected)},&key)?).await?;},
            "report"=>{c.report_performed(grpc_request(oa::ReportPerformedRequest{request_id:a.request_id.clone(),expected_revision:Some(expected)},&key)?).await?;},
            "verify"=>{c.verify(grpc_request(oa::VerifyRequest{request_id:a.request_id.clone(),expected_revision:Some(expected),evidence_ref:format!("evidence:source:{}",a.request_id)},&key)?).await?;},
            "decide"=>{c.decide_artifact(grpc_request(oa::DecideArtifactRequest{request_id:a.request_id.clone(),expected_revision:Some(expected),artifact_digest:a.artifact_digest.clone()},&key)?).await?;},
            "resolve"=>{c.resolve(grpc_request(oa::ResolveRequest{request_id:a.request_id.clone(),expected_revision:Some(expected)},&key)?).await?;},
            "ack-a"=>{c.acknowledge_continuation(grpc_request(oa::AcknowledgeContinuationRequest{request_id:a.request_id.clone(),dependent_id:"work-a".into(),expected_revision:Some(expected)},&key)?).await?;},
            _=>{c.acknowledge_continuation(grpc_request(oa::AcknowledgeContinuationRequest{request_id:a.request_id.clone(),dependent_id:"work-b".into(),expected_revision:Some(expected)},&key)?).await?;},
        }
    }
    let mut c=grpc_connect(&eps["verifier-a"].grpc).await?;
    let r=c.read_request(grpc_request(oa::RequestById{request_id:a.request_id.clone()},"rust-full-read")?).await?.into_inner();
    if r.revision!=8||r.material_revision!=1||r.created_seq==0||r.state!=oa::RequestState::Resolved as i32{return Err(format!("typed Rust protobuf request mismatch {r:?}").into())}
    let d=c.read_dependants(grpc_request(oa::RequestById{request_id:a.request_id.clone()},"rust-full-deps")?).await?.into_inner();if d.items.len()!=3{return Err("typed Rust protobuf dependants mismatch".into())}
    let ar=c.read_artifact(grpc_request(oa::ReadArtifactRequest{request_id:a.request_id.clone(),material_revision:1},"rust-full-artifact")?).await?.into_inner();
    if ar.artifact_digest!=a.artifact_digest||ar.original_bytes!=original||ar.envelope_bytes!=envelope||ar.signature!=signature{return Err("typed Rust protobuf artifact mismatch".into())}
    let lp=c.list_requests(grpc_request(oa::PageRequest{cursor:None,limit:2},"rust-full-list")?).await?.into_inner();
    let wp=c.watch(grpc_request(oa::PageRequest{cursor:None,limit:2},"rust-full-watch")?).await?.into_inner();
    if lp.items.is_empty()||wp.events.is_empty(){return Err("typed Rust protobuf list/watch pages were empty".into())}
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let grpc_target = env::var("EC00_GRPC_TARGET")?;
    let base = env::var("EC00_BASE_URL")?;
    let (endpoints, artifacts) = load_fixtures()?;
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
    http_full(&endpoints, &artifacts["rust_http"], "rust-http").await?;
    let mut empty_http=PublishRequest::new(artifacts["rust_empty_http"].request_id.clone(),artifacts["rust_empty_http"].objective.clone());
    empty_http.artifact_digest=Some(artifacts["rust_empty_http"].artifact_digest.clone()); empty_http.artifact_key_id=Some(artifacts["rust_empty_http"].key_id.clone());
    empty_http.artifact_bytes=Some(STANDARD.decode(&artifacts["rust_empty_http"].original_bytes)?); empty_http.artifact_envelope=Some(STANDARD.decode(&artifacts["rust_empty_http"].envelope_bytes)?); empty_http.artifact_signature=Some(STANDARD.decode(&artifacts["rust_empty_http"].signature)?);
    publish(&api_for(&endpoints,"coordinator-a","rust-empty-http-publish")?,empty_http).await?;
    let empty_read=read_artifact(&api_for(&endpoints,"verifier-a","rust-empty-http-read")?,&artifacts["rust_empty_http"].request_id,"1").await?;
    if !empty_read.original_bytes.is_empty()||empty_read.artifact_digest!=artifacts["rust_empty_http"].artifact_digest||empty_read.key_id!=artifacts["rust_empty_http"].key_id||empty_read.envelope_bytes!=STANDARD.decode(&artifacts["rust_empty_http"].envelope_bytes)?||empty_read.signature!=STANDARD.decode(&artifacts["rust_empty_http"].signature)?{return Err("Rust OpenAPI empty artifact bytes not preserved".into())}
    grpc_full(&endpoints,&artifacts["rust_proto"],"rust-grpc").await?;
    big_read(&endpoints,&artifacts["big_read"]).await?;
    let empty=&artifacts["rust_empty_proto"];
    let mut empty_conn=oa::owner_operations_client::OwnerOperationsClient::connect(format!("http://{}",endpoints["coordinator-a"].grpc)).await?;
    empty_conn.publish(grpc_request(oa::PublishRequest{request_id:empty.request_id.clone(),objective:Some(empty.objective.clone()),artifact_digest:Some(empty.artifact_digest.clone()),artifact_key_id:Some(empty.key_id.clone()),artifact_bytes:Some(STANDARD.decode(&empty.original_bytes)?),artifact_envelope:Some(STANDARD.decode(&empty.envelope_bytes)?),artifact_signature:Some(STANDARD.decode(&empty.signature)?),..Default::default()},"rust-empty-grpc-publish")?).await?;
    let mut read_conn=oa::owner_operations_client::OwnerOperationsClient::connect(format!("http://{}",endpoints["verifier-a"].grpc)).await?;
    let empty_proto=read_conn.read_artifact(grpc_request(oa::ReadArtifactRequest{request_id:empty.request_id.clone(),material_revision:1},"rust-empty-grpc-read")?).await?.into_inner();
    if !empty_proto.original_bytes.is_empty()||empty_proto.artifact_digest!=empty.artifact_digest||empty_proto.key_id!=empty.key_id||empty_proto.envelope_bytes!=STANDARD.decode(&empty.envelope_bytes)?||empty_proto.signature!=STANDARD.decode(&empty.signature)?{return Err("Rust protobuf empty artifact bytes not preserved".into())}
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
