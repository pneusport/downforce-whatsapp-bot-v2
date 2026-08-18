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
dados_clientes = {}
def cb_compativel(cb_jante, cb_carro):
    try:
        cb_jante = float(str(cb_jante).replace(",", "."))
        cb_carro = float(str(cb_carro).replace(",", "."))
    except (ValueError, TypeError):
        return False, False

    if cb_jante < cb_carro:
        return False, False

    if abs(cb_jante - cb_carro) < 0.05:
        return True, False

    return True, True
    def atualizar_dados_cliente(texto, sender):
        estado = dados_clientes.get(sender, {
        "marca": None,
        "modelo": None,
        "ano": None,
        "tamanho": None
    })

    texto_limpo = texto.strip()

    # Se já sabemos o carro e o cliente responde apenas 13-24,
    # assumimos que está a indicar o tamanho da jante.
    if re.fullmatch(r"(1[3-9]|2[0-4])", texto_limpo) and estado.get("ano"):
        estado["tamanho"] = texto_limpo
        dados_clientes[sender] = estado
        return estado

    prompt = f"""
Extrai apenas os dados necessários para procurar jantes.

Estado atual:
{json.dumps(estado, ensure_ascii=False)}

Nova mensagem do cliente:
{texto_limpo}

Preciso apenas de:
- marca
- modelo
- ano
- tamanho

Nunca inventes informação.

Se a nova mensagem só acrescentar um dado, mantém os dados anteriores.

Responde APENAS em JSON válido neste formato:
{{
  "marca": null,
  "modelo": null,
  "ano": null,
  "tamanho": null
}}
"""

    response = client.responses.create(
        model="gpt-5.6-luna",
        reasoning={"effort": "low"},
        input=prompt
    )

    raw = response.output_text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        novos = json.loads(raw)
    except Exception:
        novos = {}

    for campo in ["marca", "modelo", "ano", "tamanho"]:
        valor = novos.get(campo)

        if valor not in [None, "", "null"]:
            estado[campo] = str(valor).strip()

    dados_clientes[sender] = estado

    return estado
def resolver_modelos_site(marca, modelo_cliente, ano):
    base = "https://store.downforce.pt"
    url = f"{base}/ajax/produtos/utils"

    r = requests.get(
        url,
        params={
            "a": "veiculos-modelos",
            "marca": marca.upper()
        },
        timeout=20
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    modelo_procura = modelo_cliente.upper().strip()
    ano = int(ano)

    encontrados = []

    for option in soup.find_all("option"):
        modelo_site = (option.get("value") or "").strip()

        if not modelo_site:
            continue

        if modelo_procura not in modelo_site.upper():
            continue

        r_anos = requests.get(
            url,
            params={
                "a": "veiculos-anos",
                "marca": marca.upper(),
                "modelo": modelo_site
            },
            timeout=20
        )
        r_anos.raise_for_status()

        soup_anos = BeautifulSoup(r_anos.text, "html.parser")

        for option_ano in soup_anos.find_all("option"):
            intervalo = (option_ano.get("value") or "").strip()

            if "|" not in intervalo:
                continue

            inicio, fim = intervalo.split("|", 1)

            try:
                inicio = int(inicio)
                fim = int(fim)
            except ValueError:
                continue

            if inicio <= ano <= fim:
                encontrados.append({
                    "modelo": modelo_site,
                    "intervalo": intervalo
                })

    return encontrados    
def buscar_jantes_site(marca, modelo, intervalo_ano, tamanho):
    base = "https://store.downforce.pt"

    session = requests.Session()

    session.headers.update({
        "User-Agent": "Mozilla/5.0"
    })

    params = {
        "marca_vei": marca,
        "modelo_vei": modelo,
        "ano_vei": intervalo_ano
    }

    # Primeira página com o veículo selecionado
    r = session.get(
        f"{base}/pt/produtos/jantes",
        params=params,
        timeout=20
    )

    r.raise_for_status()

    resultados = []
    vistos = set()

    def processar_html(html):
        soup = BeautifulSoup(html, "html.parser")

        blocos = soup.select(".prod-list-col")

        novos = 0

        for bloco in blocos:
            stock_el = bloco.select_one(".prod-tag-stock")
            stock_texto = stock_el.get_text(" ", strip=True).lower() if stock_el else ""

            if stock_texto and "sem stock" in stock_texto:
                continue
            nome_el = bloco.select_one(".prod-list-name")
            img = bloco.select_one("img")

            if not nome_el or not img:
                continue

            nome = nome_el.get_text(" ", strip=True)

            # Só tamanho pedido pelo cliente
            if not re.search(
                rf"X{re.escape(str(tamanho))}\b",
                nome.upper()
            ):
                continue

            src = img.get("data-src") or img.get("src") or ""

            match = re.search(
                r"src=(/Imgs/produtos/[^&\"']+\.(?:jpg|jpeg|png|webp))",
                src,
                re.IGNORECASE
            )

            if match:
                image_url = urljoin(base, match.group(1))
            else:
                image_url = urljoin(base, src)

            if image_url in vistos:
                continue

            vistos.add(image_url)

            # PCD
            pcd_match = re.search(
                r"(\d+)X([\d.]+)",
                nome.upper()
            )

            # CB
            cb_match = re.search(
                r"\bCB\s*([\d.,]+)",
                nome.upper()
            )

            # ET
            et_match = re.search(
                r"\bET\s*(-?\d+(?:[.,]\d+)?)",
                nome.upper()
            )

            resultados.append({
                "nome": nome.replace("JANTE ", "", 1),
                "imagem": image_url,
                "pcd": pcd_match.group(0) if pcd_match else None,
                "cb": cb_match.group(1) if cb_match else None,
                "et": et_match.group(1) if et_match else None
            })

            novos += 1

        return len(blocos)

    # Página 1
    processar_html(r.text)

    # "Ver mais": páginas seguintes
    for pagina in range(2, 31):

        ajax_url = (
            f"{base}/ajax/produtos/jantes/"
            f"page/{pagina}/&onlyrows=true"
        )

        r = session.get(ajax_url, timeout=20)

        if r.status_code != 200:
            break

        novos = processar_html(r.text)

        if novos == 0:
            break

    print(
        "JANTES ENCONTRADAS:",
        marca,
        modelo,
        intervalo_ano,
        tamanho,
        len(resultados),
        flush=True
    )

    return resultados


def enviar_jantes_site(sender, dados):
    marca = dados["marca"]
    modelo = dados["modelo"]
    ano = dados["ano"]
    tamanho = dados["tamanho"]

    variantes = resolver_modelos_site(
        marca,
        modelo,
        int(ano)
    )

    if not variantes:
        send_message(
            sender,
            "Não encontrei opções disponíveis para esse veículo no catálogo."
        )
        return

    todas_jantes = []
    vistos = set()

    for variante in variantes:
        jantes = buscar_jantes_site(
            marca.upper(),
            variante["modelo"],
            variante["intervalo"],
            tamanho
        )

        for jante in jantes:
            chave = jante.get("imagem") or jante.get("nome")

            if chave in vistos:
                continue

            vistos.add(chave)
            todas_jantes.append(jante)

    if not todas_jantes:
        send_message(
            sender,
            f'Neste momento não encontrei jantes de {tamanho}" disponíveis para esse veículo.'
        )
        return

    send_message(
        sender,
        f"Encontrei {len(todas_jantes)} opções disponíveis. Vou mostrar até 12 modelos:"
    )

    for jante in todas_jantes[:12]:
        send_image(
            sender,
            jante["imagem"],
            jante["nome"]
        )
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
- Nunca perguntes por estilo, cor, acabamento, design ou orçamento.
- Nunca perguntes se o cliente quer pneus.
- Nunca perguntes por pneus, sensores, válvulas ou outros extras.
- Nunca perguntes pela motorização do carro.
- Nunca perguntes cilindrada, potência, combustível, versão do motor ou cavalagem.
- Nunca perguntes se o carro tem 3 portas ou 5 portas.
- Nunca perguntes o tipo de carroçaria.
- Nunca perguntes qual é a medida atualmente indicada no livrete.
- Nunca perguntes se o cliente quer receber por WhatsApp, e-mail ou outro meio.
- Nunca peças o número de WhatsApp.
- Todas as opções e fotografias devem ser enviadas diretamente nesta conversa do WhatsApp.
- Todas as jantes comercializadas pela Downforce são novas.
- Nunca perguntes se o cliente quer jantes novas ou usadas.
- Todas as jantes comercializadas pela Downforce são novas.
- Nunca inventes modelos, stock ou compatibilidades.
- Nunca perguntar referência ou fotografia de um modelo específico que pretenda?
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
                dados = atualizar_dados_cliente(text, sender)

                if not dados.get("marca"):
                    send_message(sender, "Qual é a marca do carro?")
                    return "EVENT_RECEIVED", 200

                if not dados.get("modelo"):
                    send_message(sender, "Qual é o modelo do carro?")
                    return "EVENT_RECEIVED", 200

                if not dados.get("ano"):
                    send_message(sender, "Qual é o ano do carro?")
                    return "EVENT_RECEIVED", 200

                if not dados.get("tamanho"):
                    send_message(sender, "Que tamanho de jante pretende?")
                    return "EVENT_RECEIVED", 200

                enviar_jantes_site(sender, dados)

                return "EVENT_RECEIVED", 200

            except Exception as e:
                print("ERRO PESQUISA SITE:", repr(e), flush=True)

                send_message(
                    sender,
                    "Não consegui consultar o catálogo neste momento. Por favor tente novamente."
                )

                return "EVENT_RECEIVED", 200

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
