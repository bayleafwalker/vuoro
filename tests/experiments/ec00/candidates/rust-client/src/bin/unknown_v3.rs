use std::{env, error::Error, io, time::Duration};
use ec00_oa_v3::apis::{configuration::Configuration, default_api};
use serde::Serialize;

fn observe<T: Serialize, E>(result: Result<T, E>) -> serde_json::Value {
    match result {
        Ok(value) => serde_json::json!({"decoded": true, "value": value}),
        Err(_) => serde_json::json!({"decoded": false, "error": "generated API/decoder refusal"}),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let base_url = env::var("EC00_BASE_URL")?;
    if base_url.is_empty() || env::var("EC00_UNKNOWN_VALUES_V3").as_deref() != Ok("1") {
        return Err(io::Error::other("EC00_BASE_URL and EC00_UNKNOWN_VALUES_V3=1 are required").into());
    }
    let mut configuration = Configuration::new();
    configuration.base_path = base_url.trim_end_matches('/').to_owned();
    configuration.client = reqwest::Client::builder().timeout(Duration::from_secs(5)).build()?;
    let request = default_api::read_request(&configuration, "9007199254741199").await;
    let artifact = default_api::read_artifact(&configuration, "9007199254741199", "1").await;
    println!("{}", serde_json::json!({"language": "rust", "request": observe(request), "artifact": observe(artifact)}));
    Ok(())
}
