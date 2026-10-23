{
  description = "Selected Node/Python production contract generation tools";
  # Exact source pin from the qualified EC00 v3 toolchain; no lock is resolved here.
  inputs.nixpkgs.url = "https://releases.nixos.org/nixpkgs/nixpkgs-26.11pre1093421.f20cd90e28fe/nixexprs.tar.zst";
  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.nodejs_24 ];
        shellHook = ''
          export VUORO_CONTRACTS_NODE=${pkgs.nodejs_24}/bin/node
        '';
      };
    };
}
