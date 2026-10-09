#!/usr/bin/env node
// Standalone launcher for MCP clients that are not DeepSeek Harness
// (Claude Desktop / Cursor / Codex / any stdio MCP client):
//
//   npx dsh-origin-bridge
//
// Picks the interpreter from ORIGIN_BRIDGE_PYTHON, then the venv next to
// this package, then whatever `python` is on PATH.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const server = join(root, 'server.py');

const candidates = [
  process.env.ORIGIN_BRIDGE_PYTHON,
  join(root, '.venv', 'Scripts', 'python.exe'),
  join(root, '.venv', 'bin', 'python'),
  'python',
].filter(Boolean);

const child = spawn(
  candidates.find((c) => c === 'python' || existsSync(c)) || 'python',
  ['-u', server],
  { stdio: 'inherit', cwd: root },
);

child.on('exit', (code) => process.exit(code ?? 0));
