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
          pkgs.chromium
        ];
        shellHook = ''
          export EC00_OFFLINE_PATH=${pkgs.lib.makeBinPath [ pkgs.bash pkgs.coreutils pkgs.stdenv.cc pkgs.binutils pkgs.gnumake pkgs.pkg-config pkgs.nodejs_24 pkgs.go_1_26 pkgs.rustc pkgs.cargo pkgs.protobuf pkgs.chromium ]}
          export EC00_BASH=${pkgs.bash}/bin/bash
          export EC00_ENV=${pkgs.coreutils}/bin/env
          export EC00_CHROOT=${pkgs.coreutils}/bin/chroot
          export EC00_UNSHARE=${pkgs.util-linux}/bin/unshare
          export EC00_MOUNT=${pkgs.util-linux}/bin/mount
          export EC00_IP=${pkgs.iproute2}/bin/ip
          export EC00_FHS_LIBRARY_PATH=${pkgs.lib.makeLibraryPath [ pkgs.glibc pkgs.stdenv.cc.cc.lib ]}
          export EC00_FHS_LOADER=${pkgs.glibc}/lib/ld-linux-x86-64.so.2
          export EC00_GO=${pkgs.go_1_26}/bin/go
          export EC00_CARGO=${pkgs.cargo}/bin/cargo
          export EC00_CHROMIUM=${pkgs.chromium}/bin/chromium
          export EC00_NODE=${pkgs.nodejs_24}/bin/node
          export EC00_PROTOC=${pkgs.protobuf}/bin/protoc
          export PROTOC=${pkgs.protobuf}/bin/protoc
          export PROTOC_INCLUDE=${pkgs.protobuf}/include
        '';
      };
    };
}
