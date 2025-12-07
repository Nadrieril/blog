{
  description = "Blog";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "nixpkgs/nixos-unstable";
  };

  outputs = inputs: inputs.flake-utils.lib.eachDefaultSystem (system:
    let pkgs = import inputs.nixpkgs { inherit system; };
    in
    {
      devShells.default = pkgs.mkShell {
        packages = [
          pkgs.bundler
          pkgs.jekyll
        ];
      };
    });
}
