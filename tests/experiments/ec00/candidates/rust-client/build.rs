use std::{env, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR")?);
    let proto = manifest.join("../protobuf/oa.v1.proto");
    let proto_dir = manifest.join("../protobuf");
    let include = env::var("PROTOC_INCLUDE")?;
    tonic_prost_build::configure()
        .build_server(false)
        .compile_protos(&[proto], &[proto_dir, PathBuf::from(include)])?;
    println!("cargo:rerun-if-changed={}", manifest.join("../protobuf/oa.v1.proto").display());
    Ok(())
}
