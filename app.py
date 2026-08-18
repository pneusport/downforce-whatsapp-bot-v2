from flask import Flask, request
import os
import requests
from openai import OpenAI
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "downforce2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
client = OpenAI()
conversas = {}
def buscar_jantes_site(marca, modelo, intervalo_ano, tamanho):
    url = "https://store.downforce.pt/pt/produtos/jantes"

    params = {
        "marca_vei": marca,
        "modelo_vei": modelo,
        "ano_vei": intervalo_ano
    }

    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    resultados = []
    vistos = set()

    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()

        # Só queremos produtos que sejam jantes
        if not alt.upper().startswith("JANTE "):
            continue

        # Ex.: 7X17, 8X18, 8.5X19
        if not re.search(rf"X{tamanho}\b", alt.upper()):
            continue

        src = img.get("data-src") or img.get("src")

        if not src:
            continue

        # Tenta obter a fotografia original /Imgs/produtos/...
        match = re.search(
            r"src=(/Imgs/produtos/[^&\"']+\.(?:jpg|jpeg|png|webp))",
            src,
            re.IGNORECASE
        )

        if match:
            image_url = urljoin(
                "https://store.downforce.pt",
                match.group(1)
            )
        else:
            image_url = urljoin(
                "https://store.downforce.pt",
                src
            )

        if image_url in vistos:
            continue

        vistos.add(image_url)

        nome = alt.replace("JANTE ", "", 1)

        resultados.append({
            "nome": nome,
            "imagem": image_url
        })

    return resultados
def gerar_resposta_ia(texto, sender):
    previous_id = conversas.get(sender)

    parametros = {
        "model": "gpt-5.6-luna",
        "reasoning": {"effort": "low"},
        "instructions": """
És o assistente virtual da Downforce, uma empresa portuguesa especializada em jantes automóveis.

Responde sempre em português de Portugal, de forma simpática, profissional e curta.

O teu objetivo é ajudar o cliente a encontrar jantes adequadas para o seu carro.

Quando necessário, recolhe:
1. Marca do carro
2. Modelo
3. Ano
4. Medida de jante pretendida

Faz apenas uma pergunta de cada vez.

Nunca inventes preços, stock ou compatibilidades.
Se já tens uma informação dada anteriormente pelo cliente, não a voltes a perguntar.
""",
        "input": texto
    }

    if previous_id:
        parametros["previous_response_id"] = previous_id

    response = client.responses.create(**parametros)

    conversas[sender] = response.id

    return response.output_text
@app.route("/", methods=["GET"])
def home():
    return "Downforce WhatsApp Bot está online!", 200


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    print("WEBHOOK RECEBIDO:", data, flush=True)

    try:
        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return "EVENT_RECEIVED", 200

        message = value["messages"][0]
        sender = message["from"]

        if message.get("type") == "text":
            text = message["text"]["body"].strip()

            try:
                resposta = gerar_resposta_ia(text, sender)
            except Exception as e:
                print("OPENAI ERROR:", repr(e), flush=True)
                resposta = "Desculpe, neste momento não consigo responder automaticamente. Um colaborador da Downforce irá ajudá-lo."

            send_message(sender, resposta)
    except Exception as e:
        print("ERRO:", str(e), flush=True)

    return "EVENT_RECEIVED", 200


def send_message(to, text):
    url = f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": text
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    print(
        "META RESPONSE:",
        response.status_code,
        response.text,
        flush=True
    )

    return response


def send_image(to, image_url, caption=""):
    url = f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {
            "link": image_url,
            "caption": caption
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    print(
        "META IMAGE RESPONSE:",
        response.status_code,
        response.text,
        flush=True
    )

    return response
    url = f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": text
        }
    }

    response = requests.post(url, headers=headers, json=payload)

    print("META RESPONSE:", response.status_code, response.text, flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
