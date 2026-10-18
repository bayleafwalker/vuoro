{
  description = "Pinned build tools for the disposable EC-00 Go/Rust generated clients";

  inputs.nixpkgs.url = "https://channels.nixos.org/nixpkgs-unstable/nixexprs.tar.zst";

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.go_1_26
          pkgs.rustc
          pkgs.cargo
          pkgs.jdk
          pkgs.openapi-generator-cli
          pkgs.protobuf
          pkgs.nodejs_24
        ];
        shellHook = ''
          export EC00_GO=${pkgs.go_1_26}/bin/go
          export EC00_CARGO=${pkgs.cargo}/bin/cargo
          export EC00_NODE=${pkgs.nodejs_24}/bin/node
          export EC00_PROTOC=${pkgs.protobuf}/bin/protoc
          export PROTOC=${pkgs.protobuf}/bin/protoc
          export PROTOC_INCLUDE=${pkgs.protobuf}/include
        '';
      };
    };
}
