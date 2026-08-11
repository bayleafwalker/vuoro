# vuoro-bootstrap

The filesystem-owning bootstrap distribution for Vuoro Cloud. It consumes the
public discovery and compatibility-manifest contracts without adding account,
workspace, device-authorization, or tenant state to `vuoro-client`.

The package is intentionally release-gated: an unreleased or contradictory
manifest is rejected before a local file is changed. The current development
package is a contract candidate and is not an external onboarding release.
