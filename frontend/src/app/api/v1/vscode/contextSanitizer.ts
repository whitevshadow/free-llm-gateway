/**
 * VS Code request sanitizer.
 *
 * OmniRoute stripped editor-injected context (open tabs, workspace paths) from
 * VS Code-originated requests before forwarding them. That feature is not wired
 * up here, so the request passes through unchanged — the shape is preserved so
 * src/lib/vscode/tokenizedRequest.ts keeps resolving.
 */

export function sanitizeVscodeRequest<T>(request: T): T {
  return request;
}
