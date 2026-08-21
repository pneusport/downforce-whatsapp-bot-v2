from flask import Flask, request, Response, render_template_string
import hmac
import os
import requests
import time
import json
import psycopg
from openai import OpenAI
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "downforce2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
COMERCIAL_WHATSAPP = os.getenv("351910459268", "")

client = OpenAI()
conversas = {}
dados_clientes = {}
def init_db():
    if not DATABASE_URL:
        print("DATABASE_URL não configurada", flush=True)
        return

    try:
        with psycopg.connect(DATABASE_URL) as conn:

            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversas (
                    id SERIAL PRIMARY KEY,
                    telefone TEXT UNIQUE NOT NULL,
                    nome TEXT,
                    marca TEXT,
                    modelo TEXT,
                    ano TEXT,
                    tamanho TEXT,
                    ia_ativa BOOLEAN DEFAULT TRUE,
                    estado TEXT DEFAULT 'novo',
                    ultima_mensagem TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mensagens (
                    id SERIAL PRIMARY KEY,
                    telefone TEXT NOT NULL,
                    direcao TEXT NOT NULL,
                    tipo TEXT DEFAULT 'texto',
                    conteudo TEXT,
                    imagem_url TEXT,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mensagens_processadas (
                    message_id TEXT PRIMARY KEY,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

        print("BASE DE DADOS OK", flush=True)

    except Exception as e:
        print("ERRO INIT DB:", repr(e), flush=True)
def gravar_mensagem(
    telefone,
    direcao,
    conteudo=None,
    tipo="texto",
    imagem_url=None,
    nome=None
):
    if not DATABASE_URL:
        return

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO conversas (
                        telefone,
                        nome,
                        ultima_mensagem
                    )
                    VALUES (%s, %s, CURRENT_TIMESTAMP)

                    ON CONFLICT (telefone)
                    DO UPDATE SET
                        nome = COALESCE(EXCLUDED.nome, conversas.nome),
                        ultima_mensagem = CURRENT_TIMESTAMP
                """, (telefone, nome))

                cur.execute("""
                    INSERT INTO mensagens (
                        telefone,
                        direcao,
                        tipo,
                        conteudo,
                        imagem_url
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    telefone,
                    direcao,
                    tipo,
                    conteudo,
                    imagem_url
                ))

            conn.commit()

    except Exception as e:
        print("ERRO BASE DE DADOS:", repr(e), flush=True)
def marcar_mensagem_processada(message_id):

    if not message_id:
        return False

    if not DATABASE_URL:
        print(
            "DEDUP BLOQUEADO: DATABASE_URL não configurada",
            flush=True
        )
        return False

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:

                cur.execute("""
                    INSERT INTO mensagens_processadas (message_id)
                    VALUES (%s)
                    ON CONFLICT (message_id) DO NOTHING
                    RETURNING message_id
                """, (message_id,))

                resultado = cur.fetchone()
                conn.commit()

                if resultado is not None:
                    print(
                        f"NOVA MENSAGEM: {message_id}",
                        flush=True
                    )
                    return True

                print(
                    f"MENSAGEM DUPLICADA BLOQUEADA: {message_id}",
                    flush=True
                )
                return False

        except Exception as e:
        print(
            "ERRO DEDUP WEBHOOK - BLOQUEAR POR SEGURANÇA:",
            repr(e),
            flush=True
        )
        return False


init_db()


def bmw_serie_1_a_5(dados):
    marca = str(dados.get("marca") or "").strip().lower()
    modelo = str(dados.get("modelo") or "").strip().lower()

    if marca != "bmw":
        return False

    modelo = modelo.replace("série", "serie")

    if re.search(r"\bserie\s*[1-5]\b", modelo):
        return True

    if re.search(r"\b[1-5]\d{2}[a-z]*\b", modelo):
        return True

    if re.search(r"\bm[2-5]\b", modelo):
        return True

    return False

def interpretar_configuracao_bmw(texto):
    texto = texto.lower().strip()

    if re.search(r"\b2\s*\+\s*2\b", texto):
        return "2+2"

    if any(x in texto for x in [
        "4 iguais",
        "quatro iguais",
        "4 jantes iguais",
        "todas iguais"
    ]):
        return "4_iguais"

    return None


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
def obter_serie_bmw(dados):
    marca = str(dados.get("marca") or "").strip().lower()
    modelo = str(dados.get("modelo") or "").strip().lower()

    if marca != "bmw":
        return None

    # Série 1 / Serie 1 / Series 1
    match = re.search(r"\b(?:série|serie|series)\s*([1-5])\b", modelo)
    if match:
        return int(match.group(1))

    # 118d, 120i, 218d, 320d, 420i, 530d...
    modelo_limpo = modelo.replace(" ", "")
    match = re.match(r"([1-5])\d{2}[a-z]*", modelo_limpo)

    if match:
        return int(match.group(1))

    # M2, M3, M4, M5
    match = re.match(r"m([2-5])\b", modelo_limpo)

    if match:
        return int(match.group(1))

    return None


def bmw_precisa_configuracao(dados):
    serie = obter_serie_bmw(dados)
    return serie in [1, 2, 3, 4, 5]


def interpretar_configuracao_bmw(texto):
    texto = texto.lower().strip()

    if any(x in texto for x in [
        "2+2",
        "2 + 2",
        "duas + duas",
        "duas e duas"
    ]):
        return "2+2"

    if any(x in texto for x in [
        "4 iguais",
        "quatro iguais",
        "4 jantes iguais",
        "todas iguais"
    ]):
        return "4_iguais"

    return None
def atualizar_dados_cliente(texto, sender):
    estado = dados_clientes.get(sender, {
        "marca": None,
        "modelo": None,
        "ano": None,
            # BMW Série 1 a Série 5:
            # perguntar 2+2 ou 4 iguais antes de pesquisar
            if bmw_precisa_configuracao(dados):

                configuracao = dados_clientes.get(sender, {}).get("configuracao")

                if not configuracao:
                    configuracao_resposta = interpretar_configuracao_bmw(texto_lower)

                    if configuracao_resposta:
                        dados_clientes.setdefault(sender, {})
                        dados_clientes[sender]["configuracao"] = configuracao_resposta
                        dados["configuracao"] = configuracao_resposta

                    else:
                        send_message(
                            sender,
                            "Neste BMW temos duas configurações disponíveis 😊\n\n"
                            "Pretende:\n"
                            "• 2+2 — medidas diferentes à frente e atrás\n"
                            "• 4 jantes iguais"
                        )
                        return "EVENT_RECEIVED", 200

                else:
                    dados["configuracao"] = configuracao
        "tamanho": None
    }).copy()

    texto_limpo = texto.strip()

    # Se o cliente responder apenas com um tamanho, por exemplo "15"
    if re.fullmatch(r"(1[3-9]|2[0-4])", texto_limpo):
        estado["tamanho"] = texto_limpo
        dados_clientes[sender] = estado
        return estado

    prompt = f"""
Extrai APENAS os dados que aparecem explicitamente na NOVA mensagem do cliente.

Nova mensagem:
{texto_limpo}

Quero apenas:
- marca
- modelo
- ano
- tamanho

REGRAS IMPORTANTES:

- NÃO copies dados do estado anterior.
- Se um dado não estiver escrito nesta nova mensagem, devolve null.
- Um ano deve estar normalmente entre 1900 e 2100.
- Um tamanho de jante deve estar entre 13 e 24.
- Em "Peugeot 208", 208 é o MODELO, não é o ano.
- Em "Peugeot 308", 308 é o MODELO, não é o ano.
- Em "Audi A4 2012 18", A4 é modelo, 2012 é ano e 18 é tamanho.
- Nunca inventes informação.

Responde APENAS em JSON válido:

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

    nova_marca = novos.get("marca")
    novo_modelo = novos.get("modelo")
    novo_ano = novos.get("ano")
    novo_tamanho = novos.get("tamanho")

    # Limpar valores vazios
    if nova_marca:
        nova_marca = str(nova_marca).strip()

    if novo_modelo:
        novo_modelo = str(novo_modelo).strip()

    # Validar ano
    if novo_ano:
        try:
            ano_num = int(str(novo_ano).strip())

            if 1900 <= ano_num <= 2100:
                novo_ano = str(ano_num)
            else:
                novo_ano = None
        except Exception:
            novo_ano = None

    # Validar tamanho
    if novo_tamanho:
        tamanho_match = re.search(
            r"\b(1[3-9]|2[0-4])\b",
            str(novo_tamanho)
        )

        if tamanho_match:
            novo_tamanho = tamanho_match.group(1)
        else:
            novo_tamanho = None

    # ---------------------------------------
    # DETETAR MUDANÇA DE CARRO
    # ---------------------------------------

    # Mudou de marca -> limpar carro anterior
    if (
        nova_marca
        and estado.get("marca")
        and nova_marca.lower() != estado["marca"].lower()
    ):
        estado = {
            "marca": None,
            "modelo": None,
            "ano": None,
            "tamanho": None
        }

    # Mesma marca, mas mudou de modelo
    elif (
        novo_modelo
        and estado.get("modelo")
        and novo_modelo.lower() != estado["modelo"].lower()
):
        estado["marca"] = None
        estado["modelo"] = None
        estado["ano"] = None
        estado["tamanho"] = None

    # ---------------------------------------
    # ADICIONAR APENAS OS NOVOS DADOS
    # ---------------------------------------

    if nova_marca:
        estado["marca"] = nova_marca

    if novo_modelo:
        estado["modelo"] = novo_modelo

    if novo_ano:
        estado["ano"] = novo_ano

    if novo_tamanho:
        estado["tamanho"] = novo_tamanho

    dados_clientes[sender] = estado

    print(
        "DADOS CLIENTE:",
        sender,
        estado,
        flush=True
    )

    return estado
def descobrir_marca_pelo_modelo(modelo_cliente, ano=None):
    if not modelo_cliente:
        return None

    modelo_cliente = str(modelo_cliente).strip()

    try:
        # 1. Pedir à IA apenas uma marca provável
        prompt = f"""
O cliente está a procurar jantes para um automóvel e indicou apenas o MODELO:

Modelo: {modelo_cliente}

Identifica a marca do automóvel APENAS se a relação for clara.

Exemplos:
- Clio -> Renault
- Megane -> Renault
- 208 -> Peugeot
- 308 -> Peugeot
- A3 -> Audi
- A4 -> Audi
- Golf -> Volkswagen
- Polo -> Volkswagen
- Leon -> Seat
- Ibiza -> Seat
- Corsa -> Opel
- Astra -> Opel
- Serie 3 -> BMW
- Serie 5 -> BMW

Se houver dúvida ou o modelo puder pertencer a várias marcas,
devolve null.

Não inventes.

Responde APENAS em JSON válido:

{{
    "marca": null
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
            resultado = json.loads(raw)
        except Exception:
            resultado = {}

        marca_ia = resultado.get("marca")

        if not marca_ia:
            print(
                "MARCA NÃO IDENTIFICADA PELO MODELO:",
                modelo_cliente,
                flush=True
            )
            return None

        marca_ia = str(marca_ia).strip()

        # ------------------------------------------------
        # 2. CONFIRMAR A MARCA NO SITE DOWNFORCE
        # ------------------------------------------------

        base = "https://store.downforce.pt"
        pagina_jantes = f"{base}/pt/produtos/jantes"
        utils_url = f"{base}/ajax/produtos/utils"

        session = requests.Session()

        session.headers.update({
            "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/138 Safari/537.36",

            "Accept-Language":
            "pt-PT,pt;q=0.9,en;q=0.8"
        })

        inicial = session.get(
            pagina_jantes,
            timeout=20
        )

        inicial.raise_for_status()

        headers_ajax = {
            "Referer": pagina_jantes,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01"
        }

        # ------------------------------------------------
        # 3. VER QUAL É O NOME EXATO DA MARCA NO SITE
        # ------------------------------------------------

        r_marcas = session.get(
            utils_url,
            params={
                "a": "veiculos-marcas"
            },
            headers=headers_ajax,
            timeout=20
        )

        r_marcas.raise_for_status()

        soup_marcas = BeautifulSoup(
            r_marcas.text,
            "html.parser"
        )

        marca_site = None

        marca_ia_normalizada = marca_ia.upper().strip()

        for option in soup_marcas.find_all("option"):
            valor = (option.get("value") or "").strip()

            if not valor:
                continue

            valor_normalizado = valor.upper().strip()

            if valor_normalizado == marca_ia_normalizada:
                marca_site = valor
                break

            if (
                marca_ia_normalizada in valor_normalizado
                or valor_normalizado in marca_ia_normalizada
            ):
                marca_site = valor
                break

        if not marca_site:
            print(
                "MARCA IA NÃO EXISTE NO SITE:",
                marca_ia,
                flush=True
            )
            return None

        # ------------------------------------------------
        # 4. CONFIRMAR QUE O MODELO EXISTE NESSA MARCA
        # ------------------------------------------------

        r_modelos = session.get(
            utils_url,
            params={
                "a": "veiculos-modelos",
                "marca": marca_site
            },
            headers=headers_ajax,
            timeout=20
        )

        r_modelos.raise_for_status()

        soup_modelos = BeautifulSoup(
            r_modelos.text,
            "html.parser"
        )

        modelo_procura = re.sub(
            r"\s+",
            " ",
            modelo_cliente.upper().strip()
        )

        modelos_encontrados = []

        for option in soup_modelos.find_all("option"):
            modelo_site = (option.get("value") or "").strip()

            if not modelo_site:
                continue

            modelo_site_normalizado = re.sub(
                r"\s+",
                " ",
                modelo_site.upper().strip()
            )

            if (
                modelo_site_normalizado == modelo_procura
                or modelo_site_normalizado.startswith(
                    modelo_procura + " "
                )
            ):
                modelos_encontrados.append(modelo_site)

        if not modelos_encontrados:
            print(
                "MODELO NÃO CONFIRMADO:",
                marca_site,
                modelo_cliente,
                flush=True
            )
            return None

        # ------------------------------------------------
        # 5. SE TEMOS ANO, CONFIRMAR TAMBÉM O ANO
        # ------------------------------------------------

        if ano:
            try:
                ano_num = int(str(ano).strip())
            except Exception:
                ano_num = None

            if ano_num:

                encontrado_no_ano = False

                for modelo_site in modelos_encontrados:

                    r_anos = session.get(
                        utils_url,
                        params={
                            "a": "veiculos-anos",
                            "marca": marca_site,
                            "modelo": modelo_site
                        },
                        headers=headers_ajax,
                        timeout=20
                    )

                    if r_anos.status_code != 200:
                        continue

                    soup_anos = BeautifulSoup(
                        r_anos.text,
                        "html.parser"
                    )

                    for option in soup_anos.find_all("option"):
                        intervalo = (
                            option.get("value") or ""
                        ).strip()

                        if "|" not in intervalo:
                            continue

                        inicio, fim = intervalo.split("|", 1)

                        try:
                            inicio = int(inicio)
                            fim = int(fim)
                        except ValueError:
                            continue

                        if inicio <= ano_num <= fim:
                            encontrado_no_ano = True
                            break

                    if encontrado_no_ano:
                        break

                if not encontrado_no_ano:
                    print(
                        "MODELO EXISTE MAS ANO NÃO CONFERE:",
                        marca_site,
                        modelo_cliente,
                        ano,
                        flush=True
                    )
                    return None

        print(
            "MARCA DESCOBERTA AUTOMATICAMENTE:",
            modelo_cliente,
            "->",
            marca_site,
            flush=True
        )

        return marca_site

    except Exception as e:
        print(
            "ERRO DESCOBRIR MARCA:",
            repr(e),
            flush=True
        )

        return None
def resolver_modelos_site(marca, modelo_cliente, ano):
    base = "https://store.downforce.pt"
    pagina_jantes = f"{base}/pt/produtos/jantes"
    utils_url = f"{base}/ajax/produtos/utils"

    session = requests.Session()

    session.headers.update({
        "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/138 Safari/537.36",

        "Accept-Language":
        "pt-PT,pt;q=0.9,en;q=0.8"
    })

    inicial = session.get(
        pagina_jantes,
        timeout=20
    )
    inicial.raise_for_status()

    headers_ajax = {
        "Referer": pagina_jantes,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01"
    }

    # ---------------------------------------
    # OBTER TODOS OS MODELOS REAIS DA MARCA
    # ---------------------------------------

    r = session.get(
        utils_url,
        params={
            "a": "veiculos-modelos",
            "marca": marca.upper()
        },
        headers=headers_ajax,
        timeout=20
    )

    print(
        "MODELOS SITE:",
        r.status_code,
        r.url,
        flush=True
    )

    r.raise_for_status()

    soup = BeautifulSoup(
        r.text,
        "html.parser"
    )

    modelos_site = []

    for option in soup.find_all("option"):
        modelo_site = (
            option.get("value") or ""
        ).strip()

        if modelo_site:
            modelos_site.append(modelo_site)

    if not modelos_site:
        return []

    # ---------------------------------------
    # PEDIR À IA PARA ESCOLHER MODELOS REAIS
    # ---------------------------------------

    prompt = f"""
O cliente procura jantes para este automóvel:

Marca: {marca}
Modelo dito pelo cliente: {modelo_cliente}
Ano: {ano}

Abaixo está a lista REAL de modelos disponíveis no catálogo
da marca {marca}:

{json.dumps(modelos_site, ensure_ascii=False)}

Escolhe APENAS os modelos dessa lista que podem corresponder
ao automóvel indicado pelo cliente.

Tem em conta nomes comerciais e códigos de geração.

Exemplos de interpretação:
- "Classe A" pode corresponder a um modelo da família Mercedes A
- "Classe C" pode corresponder à família Mercedes C
- "Serie 3" corresponde à família BMW Série 3
- "Golf 7" corresponde ao Golf da geração adequada
- "A4" corresponde à família Audi A4

NÃO inventes nomes.
Só podes devolver valores que existam exatamente na lista fornecida.

Se houver mais de uma geração possível, podes devolver várias.
O ano será confirmado depois pelo código.

Responde APENAS em JSON válido:

{{
    "modelos": []
}}
"""

    response = client.responses.create(
        model="gpt-5.6-luna",
        reasoning={"effort": "low"},
        input=prompt
    )

    raw = response.output_text.strip()
    raw = raw.replace(
        "```json",
        ""
    ).replace(
        "```",
        ""
    ).strip()

    try:
        escolha = json.loads(raw)
        candidatos = escolha.get("modelos", [])
    except Exception:
        candidatos = []

    # Segurança:
    # aceitar apenas nomes que existem realmente no site
    candidatos = [
        modelo
        for modelo in candidatos
        if modelo in modelos_site
    ]

    print(
        "MODELOS CANDIDATOS:",
        modelo_cliente,
        "->",
        candidatos,
        flush=True
    )

    if not candidatos:
        return []

    # ---------------------------------------
    # CONFIRMAR O ANO EM CADA CANDIDATO
    # ---------------------------------------

    try:
        ano_num = int(ano)
    except Exception:
        return []

    encontrados = []

    for modelo_site in candidatos:

        r_anos = session.get(
            utils_url,
            params={
                "a": "veiculos-anos",
                "marca": marca.upper(),
                "modelo": modelo_site
            },
            headers=headers_ajax,
            timeout=20
        )

        print(
            "ANOS SITE:",
            r_anos.status_code,
            modelo_site,
            flush=True
        )

        if r_anos.status_code != 200:
            continue

        soup_anos = BeautifulSoup(
            r_anos.text,
            "html.parser"
        )

        for option_ano in soup_anos.find_all("option"):

            intervalo = (
                option_ano.get("value") or ""
            ).strip()

            if "|" not in intervalo:
                continue

            inicio, fim = intervalo.split("|", 1)

            try:
                inicio = int(inicio)
                fim = int(fim)
            except ValueError:
                continue

            if inicio <= ano_num <= fim:

                encontrados.append({
                    "modelo": modelo_site,
                    "intervalo": intervalo
                })

                break

    print(
        "MODELOS CONFIRMADOS:",
        encontrados,
        flush=True
    )

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
            bloco_texto = bloco.get_text(" ", strip=True)
            composto = "artigo composto" in bloco_texto.lower()

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
                "imagem": imagem_url,
                "pcd": pcd_match.group(0) if pcd_match else None,
                "cb": cb_match.group(1) if cb_match else None,
                "et": et_match.group(1) if et_match else None,
                "composto": composto
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
    configuracao = dados.get("configuracao")
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
        # BMW - filtrar configuração escolhida pelo cliente
        if configuracao == "2+2":
            jantes = [
                jante for jante in jantes
                if jante.get("composto") is True
            ]

        elif configuracao == "4_iguais":
            jantes = [
                jante for jante in jantes
                if not jante.get("composto", False)
            ]    
        for jante in jantes:
            nome = (jante.get("nome") or "").strip().lower()
            imagem = (jante.get("imagem") or "").strip()

            # Usar o nome como identificador principal.
            # Se não houver nome, usar a imagem.
            chave = nome if nome else imagem

            if not chave:
                continue

            if chave in vistos:
                print(
                    f"JANTE DUPLICADA IGNORADA: {jante.get('nome')}",
                    flush=True
                )
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
        f"Encontrei {len(todas_jantes)} opções disponíveis."
    )
    if configuracao == "2+2":
        send_message(
            sender,
            "Perfeito 👍 As referências que vou enviar são para configuração *2+2*:\n"
            "2 jantes à frente + 2 jantes atrás."
        )

    elif configuracao == "4_iguais":
        send_message(
            sender,
            "Perfeito 👍 Vou enviar apenas opções para *4 jantes iguais*."
        )
    # Remover jantes/imagens duplicadas antes de enviar
    imagens_enviadas = set()
    contador = 0

    for jante in todas_jantes:

        imagem = jante.get("imagem")
        nome = jante.get("nome", "")

        if not imagem:
            continue

        if imagem in imagens_enviadas:
            print(f"IMAGEM DUPLICADA IGNORADA: {imagem}", flush=True)
            continue

        imagens_enviadas.add(imagem)

        if contador >= 25:
            break

        response = send_image(
            sender,
            imagem,
            nome
        )

        if response is not None and not response.ok:
            try:
                erro = response.json().get("error", {})

                if erro.get("code") == 131056:
                    print(
                        f"RATE LIMIT 131056 para {sender} - envio interrompido.",
                        flush=True
                    )
                    break

            except Exception:
                pass

        contador += 1
        time.sleep(0.8)

        # Depois de enviar todas as opções, fazer apenas esta pergunta
    if contador > 0:
        send_message(
            sender,
            "Gostou de alguma destas opções? 😊\n\n"
            "Se quiser, posso enviar opções noutro tamanho ou para outro carro."
        )

    print(
        f"RESPOSTA DE JANTES CONCLUÍDA PARA {sender}.",
        flush=True
    )

    return
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

        message_id = message.get("id")

        if message_id:
            primeira_vez = marcar_mensagem_processada(message_id)

            if not primeira_vez:
                print(
                    f"WEBHOOK DUPLICADO IGNORADO: {message_id}",
                    flush=True
                )
                return "EVENT_RECEIVED", 200
                if message.get("type") == "text":
                    text = message["text"]["body"].strip()
                    texto_lower = text.lower().strip()
                    # Cliente quer falar com um comercial
                    frases_comercial = [
                    "falar com comercial",
                    "falar com um comercial",
                    "quero falar com comercial",
                    "quero falar com um comercial",
                    "passa para o comercial",
                    "passar para o comercial",
                    "falar com assistente",
                    "falar com um assistente",
                    "quero falar com assistente",
                    "quero falar com um assistente",
                    "passa para um assistente"
                ]    

                pedido_comercial = any(
                    frase in texto_lower
                    for frase in frases_comercial
                )

                if pedido_comercial:
                    send_message(
                        sender,
                        "Claro 👍 Pode falar diretamente com um dos nossos comerciais aqui:\n\n"
                        f"https://wa.me/{COMERCIAL_WHATSAPP}"
                    )
                    return "EVENT_RECEIVED", 200
                    # Perguntas sobre preços
                    palavras_preco = [
                        "preço",
                        "preços",
                        "preco",
                        "precos",
                        "quanto custa",
                        "quanto custam",
                        "quanto fica",
                        "qual o valor",
                        "valor"
                     ]
    
                    pedido_preco = any(
                        palavra in texto_lower
                        for palavra in palavras_preco
                    )    

                     if pedido_preco:
                        send_message(
                            sender,
                            "Para informações sobre preços é necessário falar com um dos nossos comerciais 😊\n\n"
                            "Se quiser, responda *falar com comercial* e envio-lhe o contacto direto."
                     )
                     return "EVENT_RECEIVED", 200
                
                    # --------------------------------------------------
                    # RESPOSTAS DEPOIS DE MOSTRAR AS JANTES
                    # --------------------------------------------------

                    # Cliente quer encomendar / separar algumas jantes
                    frases_encomenda = [
                    "manda vir",
                    "é mandar vir",
                    "quero encomendar",
                    "encomenda",
                    "encomendar",
                    "separa estas",
                    "separa essas",
                    "separar estas",
                    "separar essas",
                    "manda estas",
                    "manda essas",
                    "envia estas",
                    "envia essas",
                    "pode enviar estas",
                    "podes enviar estas",
                    "pode enviar essas",
                    "podes enviar essas"
                    "manda",
                    "mandar",
                    "envia",
                    "enviar",
                    "separa",
                    "separar",
                ]
                    # Cliente quer ver mais opções, não encomendar
                                # Cliente quer ver mais opções, não encomendar
            frases_mais_opcoes = [
                "mais opções",
                "mais opcoes",
                "mais fotos",
                "envia mais",
                "manda mais",
                "mostra mais",
                "tens mais",
                "tem mais"
            ]

            pedido_mais_opcoes = any(
                frase in texto_lower
                for frase in frases_mais_opcoes
            )

            if pedido_mais_opcoes:
                send_message(
                    sender,
                    "Claro 👍 Se quiser ver mais opções, diga-me se pretende "
                    "outro tamanho ou jantes para outro carro."
                )
                return "EVENT_RECEIVED", 200

            pedido_encomenda = any(
                frase in texto_lower
                for frase in frases_encomenda
            )

            if pedido_encomenda:
                send_message(
                    sender,
                    "Obrigado pelo pedido 👍 "
                    "Vamos tratar disso e confirmar consigo a encomenda."
                )
                return "EVENT_RECEIVED", 200


            # Cliente quer ver jantes para outro carro
            outro_carro = any(
                frase in texto_lower
                for frase in [
                    "outro carro",
                    "outro veículo",
                    "outro veiculo",
                    "outra viatura",
                    "novo carro"
                ]
            )

                if outro_carro:
                    if sender in dados_clientes:
                        dados_clientes[sender].pop("configuracao", None)

                    send_message(
                        sender,
                        "Obrigado pelo pedido 👍 Vamos tratar disso."
                )
                return "EVENT_RECEIVED", 200


            # Cliente quer outro tamanho para o mesmo carro
            outro_tamanho = any(
                frase in texto_lower
                for frase in [
                    "outro tamanho",
                    "noutro tamanho",
                    "outra medida",
                    "noutra medida"
                ]
            )

            if outro_tamanho:
                if sender in dados_clientes:
                    dados_clientes[sender]["tamanho"] = None

                send_message(
                    sender,
                    "Claro 👍 Que tamanho de jante pretende ver?"
                )
                return "EVENT_RECEIVED", 200

            # Se apenas agradecer, responder e TERMINAR
            if texto_lower in [
                "obrigado",
                "obrigada",
                "obg",
                "thanks"
            ]:
                send_message(
                    sender,
                    "De nada 😊 Estamos disponíveis!"
                )
                return "EVENT_RECEIVED", 200
            cumprimentos = [
                "olá",
                "ola",
                "boas",
                "bom dia",
                "boa tarde",
                "boa noite"
            ]

            if texto_lower in cumprimentos:
                send_message(
                    sender,
                    "Olá! 👋 Bem-vindo à Downforce.\n\n"
                    "Estou aqui para ajudar a encontrar jantes compatíveis para o seu carro.\n"
                    "Qual é a marca e o modelo?"
                )
                return "EVENT_RECEIVED", 200
            try:
                dados = atualizar_dados_cliente(text, sender)
                    # Ver se o cliente acabou de responder 2+2 ou 4 iguais
                    configuracao_bmw = interpretar_configuracao_bmw(texto_lower)

                    if configuracao_bmw:
                    dados_clientes.setdefault(sender, {})
                    dados_clientes[sender]["configuracao"] = configuracao_bmw
                    dados["configuracao"] = configuracao_bmw

                    # Recuperar configuração previamente escolhida
                    configuracao_guardada = dados_clientes.get(sender, {}).get("configuracao")

                    if configuracao_guardada:
                    dados["configuracao"] = configuracao_guardada

                if not dados.get("marca") and dados.get("modelo"):
                    marca_encontrada = descobrir_marca_pelo_modelo(
                        dados["modelo"],
                        dados.get("ano")
                    )

                    if marca_encontrada:
                        dados["marca"] = marca_encontrada
                        dados_clientes[sender]["marca"] = marca_encontrada

                if not dados.get("marca"):
                    send_message(
                        sender,
                        "Só preciso de confirmar uma coisa 😊 Qual é a marca do carro?"
                    )
                    return "EVENT_RECEIVED", 200

                if not dados.get("modelo"):
                    send_message(
                        sender,
                        "Obrigado 😊 Qual é o modelo do carro?"
                    )
                    return "EVENT_RECEIVED", 200

                if not dados.get("ano"):
                    send_message(
                        sender,
                        "Perfeito 👍 E de que ano é o carro?"
                    )
                    return "EVENT_RECEIVED", 200
                # BMW Série 1 a 5 - perguntar configuração antes de procurar jantes
                if bmw_serie_1_a_5(dados) and not dados.get("configuracao"):
                    send_message(
                        sender,
                        "Para este BMW preciso de confirmar a configuração 😊\n\n"
                        "Pretende:\n"
                        "• *2+2* — 2 jantes à frente + 2 jantes atrás\n"
                        "• *4 iguais* — as 4 jantes com a mesma medida"
                    )
                    return "EVENT_RECEIVED", 200
                if not dados.get("tamanho"):
                    send_message(
                        sender,
                        'Ótimo 😊 Que tamanho de jante pretende? Por exemplo: 15", 16", 17", 18"...'
                    )
                    return "EVENT_RECEIVED", 200

                enviar_jantes_site(sender, dados)
                return "EVENT_RECEIVED", 200

            except Exception as e:
                print(
                    "ERRO PESQUISA SITE:",
                    repr(e),
                    flush=True
                )

                send_message(
                    sender,
                    "Peço desculpa 😊 Neste momento não consegui consultar o catálogo. "
                    "Pode tentar novamente dentro de alguns instantes?"
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

    if response.ok:
        gravar_mensagem(
            to,
            "saida",
            conteudo=text,
            tipo="texto"
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

    if response.ok:
        gravar_mensagem(
            to,
            "saida",
            conteudo=caption,
            tipo="imagem",
            imagem_url=image_url
        )
        return response

    try:
        erro_meta = response.json().get("error", {})
        codigo_erro = erro_meta.get("code")
    except Exception:
        codigo_erro = None

    if codigo_erro == 131056:
        print(
            f"RATE LIMIT 131056 para {to} - parar envio de imagens.",
            flush=True
        )

    return response

# ==========================================================
# ADMIN - CONVERSAS WHATSAPP
# ==========================================================

def admin_autorizado():
    auth = request.authorization

    if not auth:
        return False

    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")

    return (
        hmac.compare_digest(auth.username or "", admin_user)
        and hmac.compare_digest(auth.password or "", admin_password)
    )


def pedir_login_admin():
    return Response(
        "Login necessário",
        401,
        {"WWW-Authenticate": 'Basic realm="Downforce Admin"'}
    )


ADMIN_HTML = """
<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="5">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>Downforce WhatsApp Admin</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f0f2f5;
            color: #111;
        }

        .topo {
            height: 64px;
            background: #111827;
            color: white;
            display: flex;
            align-items: center;
            padding: 0 25px;
            font-size: 22px;
            font-weight: bold;
        }

        .layout {
            display: flex;
            height: calc(100vh - 64px);
        }

        .conversas {
            width: 340px;
            background: white;
            border-right: 1px solid #ddd;
            overflow-y: auto;
        }

        .titulo {
            padding: 18px;
            font-size: 18px;
            font-weight: bold;
            border-bottom: 1px solid #ddd;
        }

        .cliente {
            display: block;
            padding: 15px;
            border-bottom: 1px solid #eee;
            text-decoration: none;
            color: #111;
        }

        .cliente:hover {
            background: #f5f5f5;
        }

        .cliente.ativo {
            background: #e7f5ef;
        }

        .nome {
            font-weight: bold;
            margin-bottom: 5px;
        }

        .telefone {
            font-size: 13px;
            color: #666;
        }

        .hora {
            font-size: 11px;
            color: #999;
            margin-top: 5px;
        }

        .chat {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #efeae2;
        }

        .cabecalho-chat {
            background: white;
            padding: 15px 20px;
            border-bottom: 1px solid #ddd;
            font-weight: bold;
        }

        .mensagens {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
        }

        .linha {
            display: flex;
            margin-bottom: 10px;
        }

        .entrada {
            justify-content: flex-start;
        }

        .saida {
            justify-content: flex-end;
        }

        .bolha {
            max-width: 70%;
            padding: 10px 12px;
            border-radius: 8px;
            line-height: 1.4;
            box-shadow: 0 1px 2px rgba(0,0,0,.15);
        }

        .entrada .bolha {
            background: white;
        }

        .saida .bolha {
            background: #d9fdd3;
        }

        .mensagem-hora {
            margin-top: 5px;
            font-size: 10px;
            color: #777;
            text-align: right;
        }

        .imagem-chat {
            max-width: 350px;
            width: 100%;
            border-radius: 6px;
            margin-bottom: 7px;
            display: block;
        }

        .sem-chat {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #666;
            font-size: 18px;
        }

        @media(max-width: 800px) {
            .conversas {
                width: 260px;
            }

            .bolha {
                max-width: 85%;
            }
        }
    </style>
</head>

<body>

<div class="topo">
    Downforce - WhatsApp Admin
</div>

<div class="layout">

    <div class="conversas">

        <div class="titulo">
            Conversas
        </div>

        {% for conversa in conversas %}

            <a
                href="/admin/{{ conversa[0] }}"
                class="cliente {% if telefone_selecionado == conversa[0] %}ativo{% endif %}"
            >

                <div class="nome">
                    {{ conversa[1] or conversa[0] }}
                </div>

                <div class="telefone">
                    +{{ conversa[0] }}
                </div>

                <div class="hora">
                    {{ conversa[2] }}
                </div>

            </a>

        {% endfor %}

    </div>


    {% if telefone_selecionado %}

    <div class="chat">

        <div class="cabecalho-chat">
            {{ nome_selecionado or telefone_selecionado }}
            &nbsp; | &nbsp;
            +{{ telefone_selecionado }}
        </div>

        <div class="mensagens" id="mensagens">

            {% for mensagem in mensagens %}

                <div class="linha {{ 'saida' if mensagem[1] == 'saida' else 'entrada' }}">

                    <div class="bolha">

                        {% if mensagem[4] %}
                            <img
                                class="imagem-chat"
                                src="{{ mensagem[4] }}"
                                loading="lazy"
                            >
                        {% endif %}

                        {% if mensagem[3] %}
                            <div>{{ mensagem[3] }}</div>
                        {% endif %}

                        <div class="mensagem-hora">
                            {{ mensagem[5] }}
                        </div>

                    </div>

                </div>

            {% endfor %}

        </div>

    </div>

    {% else %}

    <div class="sem-chat">
        Seleciona uma conversa
    </div>

    {% endif %}

</div>


<script>
    const mensagens = document.getElementById("mensagens");

    if (mensagens) {
        mensagens.scrollTop = mensagens.scrollHeight;
    }
</script>

</body>
</html>
"""


@app.route("/admin")
@app.route("/admin/<telefone>")
def admin(telefone=None):

    if not admin_autorizado():
        return pedir_login_admin()

    if not DATABASE_URL:
        return "DATABASE_URL não configurada", 500

    conversas = []
    mensagens = []
    nome_selecionado = None

    try:

        with psycopg.connect(DATABASE_URL) as conn:

            with conn.cursor() as cur:

                # Lista das conversas
                cur.execute("""
                    SELECT
                        telefone,
                        nome,
                        ultima_mensagem
                    FROM conversas
                    ORDER BY ultima_mensagem DESC
                    LIMIT 300
                """)

                conversas = cur.fetchall()

                # Mensagens da conversa selecionada
                if telefone:

                    cur.execute("""
                        SELECT
                            id,
                            direcao,
                            tipo,
                            conteudo,
                            imagem_url,
                            criado_em
                        FROM mensagens
                        WHERE telefone = %s
                        ORDER BY criado_em ASC, id ASC
                        LIMIT 1000
                    """, (telefone,))

                    mensagens = cur.fetchall()

                    cur.execute("""
                        SELECT nome
                        FROM conversas
                        WHERE telefone = %s
                    """, (telefone,))

                    resultado = cur.fetchone()

                    if resultado:
                        nome_selecionado = resultado[0]

    except Exception as e:
        print("ERRO ADMIN:", repr(e), flush=True)
        return f"Erro ao carregar admin: {e}", 500

    return render_template_string(
        ADMIN_HTML,
        conversas=conversas,
        mensagens=mensagens,
        telefone_selecionado=telefone,
        nome_selecionado=nome_selecionado
    )
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
