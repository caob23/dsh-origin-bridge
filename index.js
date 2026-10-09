// dsh bundle entry point.
//
// The Origin tools are NOT implemented here: they come from server.py, which
// cordis.patch.yml registers through @deepseek-ai/dsh-mcp-client. This module
// exists so the bundle has a loadable main and so dsh's plugin market sees the
// package as live after installation. Keep apply() side-effect free — anything
// done here runs on every dsh startup.

export const name = 'dsh-origin-bridge';

export function activate() {}

export function apply() {}

export default { name, activate, apply };
