const TERMINAL_SCOPE = /^(session|run|harness|event|service|auth):/;

export function isTerminalToken(token) {
  const name = String(token?.name || '').toLowerCase();
  const scopes = Array.isArray(token?.scopes) ? token.scopes : [];
  return name.startsWith('terminal client') || scopes.some(scope => TERMINAL_SCOPE.test(String(scope || '')));
}

export function terminalLoginCommand(token, scopes) {
  const scopeList = Array.isArray(scopes) ? scopes.join(',') : '';
  return `ody-term auth login \\\n  --token '${token}' \\\n  --scopes '${scopeList}'`;
}

export async function createTerminalToken(fetchImpl, name) {
  const fd = new FormData();
  fd.append('name', String(name || '').trim() || 'Terminal Client');
  fd.append('profile', 'terminal');
  const response = await fetchImpl('/api/tokens', {
    method: 'POST',
    credentials: 'same-origin',
    body: fd,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || 'Token creation failed');
  if (typeof payload.token !== 'string' || !Array.isArray(payload.scopes)) {
    throw new Error('Token creation returned an invalid response');
  }
  return {...payload, command: terminalLoginCommand(payload.token, payload.scopes)};
}

export async function copyTerminalCommand(clipboard, command) {
  await clipboard.writeText(command);
}

export function clearTerminalSecrets(root) {
  for (const selector of ['#uf-terminal-token', '#uf-terminal-command']) {
    const field = root?.querySelector?.(selector);
    if (field) field.textContent = '';
  }
}
