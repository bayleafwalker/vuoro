use std::{env, error::Error, time::Duration};

use prost::Message;
use serde::Serialize;
use tonic::{transport::Endpoint, Request};

pub mod oa {
    tonic::include_proto!("ec00.oa.v1");
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ResultRow {
    route: String,
    #[serde(rename = "case")]
    case_name: String,
    request_hex: String,
    response_hex: Option<String>,
    status: String,
}

struct InputCase {
    name: &'static str,
    cursor: Option<String>,
    limit: Option<i32>,
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(DIGITS[(byte >> 4) as usize] as char);
        encoded.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    encoded
}

fn input_cases() -> Vec<InputCase> {
    vec![
        InputCase { name: "omitted", cursor: None, limit: None },
        InputCase { name: "zero", cursor: None, limit: Some(0) },
        InputCase { name: "empty", cursor: Some(String::new()), limit: None },
        InputCase { name: "one", cursor: None, limit: Some(1) },
        InputCase { name: "negative", cursor: None, limit: Some(-1) },
        InputCase { name: "oversized", cursor: None, limit: Some(3) },
    ]
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let target = env::args().nth(1).ok_or("usage: cargo run --bin read_presence -- <grpc-target>")?;
    let endpoint = Endpoint::from_shared(format!("http://{target}"))?
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(5));
    let channel = endpoint.connect().await?;
    let mut client = oa::owner_operations_client::OwnerOperationsClient::new(channel);
    let mut rows = Vec::with_capacity(12);

    for route in ["list-requests", "watch"] {
        for test_case in input_cases() {
            let input = oa::PageRequestV2 {
                cursor: test_case.cursor,
                limit: test_case.limit,
            };
            let request_hex = hex(&input.encode_to_vec());
            let mut request = Request::new(input);
            request.set_timeout(Duration::from_secs(5));

            let (response_hex, status) = if route == "list-requests" {
                match client.list_requests_v2(request).await {
                    Ok(response) => (Some(hex(&response.into_inner().encode_to_vec())), "OK".to_owned()),
                    Err(status) => (None, format!("{:?}", status.code())),
                }
            } else {
                match client.watch_v2(request).await {
                    Ok(response) => (Some(hex(&response.into_inner().encode_to_vec())), "OK".to_owned()),
                    Err(status) => (None, format!("{:?}", status.code())),
                }
            };

            rows.push(ResultRow {
                route: route.to_owned(),
                case_name: test_case.name.to_owned(),
                request_hex,
                response_hex,
                status,
            });
        }
    }

    println!("{}", serde_json::to_string(&rows)?);
    Ok(())
}
