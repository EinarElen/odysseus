import json
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SETTINGS_JS = REPO / "static" / "js" / "settings.js"
INTEGRATION_JS = REPO / "static" / "js" / "terminalClientIntegration.js"


def _run_module(script: str) -> dict[str, object]:
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_create_terminal_token_contract() -> None:
    module_url = INTEGRATION_JS.as_uri()
    payload = _run_module(
        f"""
        import {{createTerminalToken}} from {json.dumps(module_url)};
        let request;
        const created = await createTerminalToken(async (url, options) => {{
          request = {{url, method: options.method, credentials: options.credentials,
            name: options.body.get('name'), profile: options.body.get('profile')}};
          return {{ok: true, json: async () => ({{
            token: 'ody_real_token', scopes: ['event:read', 'run:start']
          }})}};
        }}, 'Terminal Client — laptop');
        let failure = '';
        try {{
          await createTerminalToken(async () => ({{
            ok: false, json: async () => ({{detail: 'not allowed'}})
          }}), 'denied');
        }} catch (error) {{ failure = error.message; }}
        console.log(JSON.stringify({{request, created, failure}}));
        """
    )

    assert payload["request"] == {
        "url": "/api/tokens",
        "method": "POST",
        "credentials": "same-origin",
        "name": "Terminal Client — laptop",
        "profile": "terminal",
    }
    assert payload["created"]["command"] == (
        "ody-term auth login \\\n"
        "  --token 'ody_real_token' \\\n"
        "  --scopes 'event:read,run:start'"
    )
    assert payload["failure"] == "not allowed"


def test_terminal_token_helpers() -> None:
    module_url = INTEGRATION_JS.as_uri()
    payload = _run_module(
        f"""
        import {{clearTerminalSecrets, copyTerminalCommand, isTerminalToken}} from {json.dumps(module_url)};
        const fields = {{
          '#uf-terminal-token': {{textContent: 'ody_secret'}},
          '#uf-terminal-command': {{textContent: 'ody-term auth login --token ody_secret'}},
        }};
        clearTerminalSecrets({{querySelector: selector => fields[selector]}});
        let copiedAfterCleanup = 'not-called';
        await copyTerminalCommand(
          {{writeText: async value => {{ copiedAfterCleanup = value; }}}},
          fields['#uf-terminal-command'].textContent,
        );
        let copyFailure = '';
        try {{
          await copyTerminalCommand({{writeText: async () => {{ throw new Error('clipboard denied'); }}}}, 'command');
        }} catch (error) {{ copyFailure = error.message; }}
        console.log(JSON.stringify({{
          byName: isTerminalToken({{name: 'Terminal Client <img src=x>', scopes: []}}),
          byScope: isTerminalToken({{name: 'custom', scopes: ['run:start']}}),
          notTerminal: isTerminalToken({{name: 'Codex Agent', scopes: ['chat']}}),
          tokenText: fields['#uf-terminal-token'].textContent,
          commandText: fields['#uf-terminal-command'].textContent,
          copiedAfterCleanup,
          copyFailure,
        }}));
        """
    )

    assert payload == {
        "byName": True,
        "byScope": True,
        "notTerminal": False,
        "tokenText": "",
        "commandText": "",
        "copiedAfterCleanup": "",
        "copyFailure": "clipboard denied",
    }


def test_terminal_settings_wiring_escapes_text() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")

    assert "['terminal', 'Terminal Client']" in source
    assert "showTerminalForm(editId)" in source
    assert "type === 'terminal'" in source
    assert "${esc(item.name)}" in source
    assert "${esc(item.detail || '')}" in source
    assert ">${item.name} " not in source
    assert "clearTerminalSecrets(modalEl)" in source
    assert "const command = el('uf-terminal-command')?.textContent || '';" in source
