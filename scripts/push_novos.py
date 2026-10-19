#!/usr/bin/env python3
"""Avisa o app, por um push silencioso do FCM, quando o feed publicado ganha concursos novos.

Roda no workflow diário logo depois de publicar o docs/concursos.json. Compara os ids do feed novo com os do
feed publicado antes desta execução e, se houver ids novos, manda UMA mensagem ao tópico "novos-concursos"
do projeto Firebase "concursos-publicos":

- só dados (sem alerta): `apns-push-type: background`, `apns-priority: 5` e `aps.content-available = 1`, para o
  iOS acordar o app em segundo plano e ele mesmo baixar o feed e decidir o que notificar;
- campo `ids` com os ids novos separados por vírgula (cortado em MAX_IDS_CHARS) e `count` com o total.

Autenticação: conta de serviço do Firebase no secret FIREBASE_SERVICE_ACCOUNT (o JSON inteiro). O script troca
um JWT assinado (RS256) por um access token OAuth2 (fluxo JWT bearer) e chama a API HTTP v1 do FCM. A assinatura
usa o `openssl` da linha de comando, que já vem no runner do GitHub, então continua sem `pip install`.

Sem o secret, o script registra no log e sai com 0: o push é opcional e nunca impede o feed.

Uso:
    python3 scripts/push_novos.py --previous /tmp/anterior.json docs/concursos.json [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

PROJECT_ID = "concursos-publicos"
TOPIC = "novos-concursos"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
# O payload de dados do FCM tem limite de 4 KB; 1.000 caracteres cabem ~76 ids de 12 caracteres.
MAX_IDS_CHARS = 1000


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- ids novos

def feed_ids(feed: object) -> list[str]:
    """Ids do feed na ordem em que aparecem (o feed vem do mais recente para o mais antigo)."""
    if not isinstance(feed, list):
        return []
    return [i["id"] for i in feed if isinstance(i, dict) and isinstance(i.get("id"), str) and i["id"]]


def new_ids(previous: object, current: object) -> list[str]:
    """Ids do feed atual que não estavam no publicado antes, sem repetição e na ordem do feed atual."""
    before = set(feed_ids(previous))
    out: list[str] = []
    for id_ in feed_ids(current):
        if id_ not in before and id_ not in out:
            out.append(id_)
    return out


def join_ids(ids: list[str], limit: int = MAX_IDS_CHARS) -> str:
    """"a,b,c" com no máximo `limit` caracteres, sem cortar um id no meio."""
    text = ""
    for id_ in ids:
        candidate = f"{text},{id_}" if text else id_
        if len(candidate) > limit:
            break
        text = candidate
    return text


def build_message(ids: list[str], topic: str = TOPIC) -> dict:
    """Corpo do POST para messages:send: push silencioso, só dados."""
    return {
        "message": {
            "topic": topic,
            # Os valores de `data` no FCM precisam ser texto.
            "data": {"ids": join_ids(ids), "count": str(len(ids))},
            "apns": {
                "headers": {"apns-push-type": "background", "apns-priority": "5"},
                "payload": {"aps": {"content-available": 1}},
            },
        }
    }


# ---------------------------------------------------------------- OAuth2 (JWT bearer)

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def openssl_sign(private_key_pem: str, data: bytes) -> bytes:
    """Assinatura RS256 (RSASSA-PKCS1-v1_5 com SHA-256) pelo openssl. A chave fica num arquivo temporário 0600."""
    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / "key.pem"
        key.touch(mode=0o600)
        key.write_text(private_key_pem)
        result = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key)], input=data,
                                capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"openssl não assinou o JWT: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def build_jwt(account: dict, now: int, sign: Callable[[str, bytes], bytes] = openssl_sign) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    if account.get("private_key_id"):
        header["kid"] = account["private_key_id"]
    claims = {
        "iss": account["client_email"],
        "scope": SCOPE,
        "aud": account.get("token_uri") or TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}.{b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    return f"{signing_input}.{b64url(sign(account['private_key'], signing_input.encode()))}"


def post(url: str, body: bytes, headers: dict) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {err.code} em {url}: {detail}") from err


def access_token(account: dict, now: int, *, sign=openssl_sign, send=post) -> str:
    assertion = build_jwt(account, now, sign)
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}).encode()
    data = send(account.get("token_uri") or TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded"})
    if not data.get("access_token"):
        raise RuntimeError("o Google não devolveu access_token")
    return data["access_token"]


def send_message(message: dict, account: dict, project: str, *, now: int | None = None, sign=openssl_sign, send=post) -> dict:
    token = access_token(account, int(now if now is not None else time.time()), sign=sign, send=send)
    return send(FCM_URL.format(project=project), json.dumps(message).encode(),
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})


# ---------------------------------------------------------------- main

def load_account(raw: str | None) -> dict | None:
    """O JSON da conta de serviço; None quando o secret não existe ou está vazio."""
    if not raw or not raw.strip():
        return None
    account = json.loads(raw)
    missing = [k for k in ("client_email", "private_key") if not account.get(k)]
    if missing:
        raise ValueError(f"FIREBASE_SERVICE_ACCOUNT sem {', '.join(missing)}")
    return account


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("feed", type=Path, nargs="?", default=Path("docs/concursos.json"))
    parser.add_argument("--previous", type=Path, required=True, help="feed publicado antes desta execução")
    parser.add_argument("--project", default=PROJECT_ID, help=f"id do projeto Firebase (padrão: {PROJECT_ID})")
    parser.add_argument("--dry-run", action="store_true", help="só mostra a mensagem, sem autenticar nem enviar")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    # Sem o feed anterior não dá para saber o que é novo: melhor não avisar do que avisar o feed inteiro.
    if not args.previous.exists():
        log(f"push: {args.previous} não existe; nada a comparar")
        return 0
    ids = new_ids(json.loads(args.previous.read_text()), json.loads(args.feed.read_text()))
    if not ids:
        log("push: nenhum concurso novo; nada enviado")
        return 0
    message = build_message(ids)
    log(f"push: {len(ids)} concurso(s) novo(s) para o tópico {TOPIC!r}")
    if args.dry_run:
        print(json.dumps(message, ensure_ascii=False, indent=1))
        return 0

    account = load_account(env.get("FIREBASE_SERVICE_ACCOUNT"))
    if account is None:
        log("push: secret FIREBASE_SERVICE_ACCOUNT não definido; push pulado")
        return 0
    result = send_message(message, account, args.project)
    log(f"push: enviado ({result.get('name', 'sem nome')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
