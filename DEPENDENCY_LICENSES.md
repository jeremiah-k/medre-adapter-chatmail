# Runtime dependency licenses

The `LICENSE` in this repository covers original MEDRE-authored adapter code
(MIT). It does not change the license of imported SDK code.

- `deltachat-rpc-client==2.65.0`: MPL-2.0 license — [release metadata](https://pypi.org/project/deltachat-rpc-client/2.65.0/).
- `deltachat-rpc-server==2.65.0`: MPL-2.0 notice in the release wheel at
  `deltachat_rpc_server/LICENSE` — [release artifacts](https://pypi.org/project/deltachat-rpc-server/2.65.0/).
  Its package metadata omits the license expression; that omission does not
  remove the packaged notice. The [matching native source](https://github.com/chatmail/core/tree/v2.65.0)
  also declares MPL-2.0. Bundled native dependencies retain their own terms.
- Review the full resolved environment and retain any required third-party
  notices when packaging a deployment.

See [MEDRE licensing](https://github.com/jeremiah-k/medre/blob/main/docs/dev/licensing.md).
