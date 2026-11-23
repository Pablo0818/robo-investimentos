# Robô de Investimentos: guia passo a passo

Este guia leva você do zero até o robô rodando 24 horas. Faça **na ordem**. Só passe para dinheiro real depois de completar a Fase 5.

> Este sistema é para operar **o seu próprio dinheiro**. Para gerir dinheiro de terceiros, a CVM exige autorização prévia de administrador de carteira (Resolução CVM 21). Nenhum robô garante lucro. Comece pequeno.

---

## O que você está recebendo

| Parte | O que faz |
|---|---|
| **Robô 24h** | Lê o mercado a cada minuto, gera sinais de compra e venda e protege as posições com stop |
| **Modo manual** | Cada ordem chega no painel e no Telegram com os botões ✅ Aprovar / ❌ Recusar |
| **Modo automático** | O robô compra e vende sozinho, dentro dos seus limites de risco |
| **Evolução automática** | Testa centenas de estratégias no histórico e valida cada uma num período que ela nunca viu. Só troca a estratégia ativa quando a nova é claramente melhor |
| **Reinvestimento** | O lucro aumenta o tamanho das próximas posições (juros compostos). Dá para desligar |
| **Gestão de risco** | 1% de risco por operação, no máximo 25% por ativo, limite de perda diária, e trava total se o patrimônio cair 15% desde o topo |
| **Mercados** | Cripto (24h/7d, Binance), B3 (MetaTrader 5, das 10h às 17h) e EUA (Alpaca) |
| **Pesquisa EUA** | As 11 ferramentas do trader-skill: copiar investidor famoso (13F), notícias, trades do Congresso, analista de resultados, filtro de ações, rebalanceamento, impostos e alertas |
| **Finanças** | Patrimônio, lucro, aportes e retiradas, e resultado mensal (base para o DARF) |

---

## Fase 1: testar no seu computador (15 minutos, sem conta nenhuma)

### 1.1 Instale o Python
1. Acesse **python.org/downloads** e baixe o Python 3.12.
2. **Windows:** na primeira tela do instalador, marque **"Add python.exe to PATH"**. Depois clique em *Install Now*.
3. Para conferir, abra o terminal (Windows: tecla Windows, digite `cmd`) e rode `python --version`.

### 1.2 Prepare o projeto
1. Extraia o arquivo `robo-investimentos.zip` numa pasta, por exemplo `C:\robo`.
2. Copie o arquivo `.env.example` e renomeie a cópia para `.env`.
3. Abra o `.env` no Bloco de Notas e altere:
   ```
   DASHBOARD_PASSWORD=uma-senha-forte-sua
   BROKER=synthetic
   ```
   (`synthetic` é um mercado de mentira, só para você conhecer o sistema.)

### 1.3 Ligue o robô
- **Windows:** dê dois cliques em `iniciar-windows.bat`. Na primeira vez ele instala tudo, o que leva de 2 a 5 minutos.
- **Mac/Linux:** rode `./iniciar.sh`.

### 1.4 Abra o painel
Acesse **http://localhost:8000** e entre com o usuário `pablo` (ou o que estiver em `DASHBOARD_USER`) e a sua senha.

Explore as abas. Aprove e recuse alguns sinais. Teste o botão **Travar**.

---

## Fase 2: cripto com dados reais, ainda em simulação

Nesta fase não precisa de conta nem de chave. O robô usa preços reais da Binance e dinheiro fictício.

1. Pare o robô (feche a janela ou use Ctrl+C).
2. No `.env`, deixe assim:
   ```
   TRADING_MODE=paper
   BROKER=crypto
   SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
   ```
3. Para recomeçar a simulação do zero, apague a pasta `data`.
4. Ligue o robô de novo. Na primeira vez ele roda a evolução e escolhe uma estratégia para cada moeda (veja a aba **Estratégias**).

> Se uma moeda aparecer como "sem estratégia aprovada", é proteção. Nenhuma estratégia passou no teste fora da amostra, então o robô fica fora daquela moeda e tenta de novo a cada 6 horas.

---

## Fase 3: criar as contas

### 3.1 Telegram (para aprovar ordens pelo celular)
1. No Telegram, procure **@BotFather** e envie `/newbot`.
2. Escolha um nome e um usuário terminado em `bot`. O BotFather responde com um **token** (algo como `123456:ABC...`).
3. Abra uma conversa com o seu bot novo e envie "oi".
4. No navegador, acesse `https://api.telegram.org/botSEU_TOKEN/getUpdates`. Procure `"chat":{"id":` e copie o número que vem depois.
5. No `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```
6. Reinicie o robô. Você vai receber "🤖 Robô ligado". Os comandos disponíveis são `/status`, `/pendentes`, `/pausar`, `/retomar` e `/travar`.

### 3.2 Binance (cripto, 24h)

**Primeiro, a conta de teste (testnet), com dinheiro de mentira:**
1. Acesse **testnet.binance.vision** e entre com a sua conta do GitHub.
2. Clique em **Generate HMAC_SHA256 Key**, dê um nome e copie a **API Key** e a **Secret Key**.
3. No `.env`:
   ```
   TRADING_MODE=live
   BROKER=crypto
   EXCHANGE_API_KEY=sua_api_key_testnet
   EXCHANGE_SECRET=sua_secret_testnet
   EXCHANGE_TESTNET=true
   ```
   Aqui as ordens são enviadas de verdade, mas para a corretora de teste. Assim você testa a conexão real sem risco.

**Depois, a conta real (só na Fase 5):**
1. Crie a conta em **binance.com**, faça a verificação de identidade (KYC) e **ative a autenticação em 2 fatores**.
2. Vá em Perfil → **Gerenciamento de API** → Criar API (tipo "Gerada pelo sistema").
3. Nas permissões, marque **somente** "Ativar negociação Spot e de Margem". **NUNCA** ative "Permitir saques".
4. Em "Restringir acesso a IPs confiáveis", coloque o IP do seu servidor (Fase 4).
5. Troque as chaves no `.env` e coloque `EXCHANGE_TESTNET=false`.
6. Deposite em USDT (via Pix, na própria Binance) o valor que você aceita arriscar.

### 3.3 Corretora da B3 com MetaTrader 5 (ações, mini índice, mini dólar)
O MetaTrader 5 só funciona no **Windows**.
1. Abra conta numa corretora que ofereça MetaTrader 5 para B3, como **Genial**, **Clear** ou **Rico**. Confirme no site da corretora que o MT5 está disponível para o seu tipo de conta e se há custo de plataforma.
2. Peça a liberação do MetaTrader 5. A maioria oferece **conta demo**: comece por ela.
3. Instale o MetaTrader 5 pelo link da corretora, faça login e deixe o programa aberto.
4. No MT5, ligue o botão **"Algo Trading"** (fica verde) e vá em Ferramentas → Opções → Expert Advisors → marque "Permitir negociação algorítmica".
5. No `.env`:
   ```
   BROKER=mt5
   SYMBOLS=PETR4,VALE3,ITUB4
   TIMEFRAME=1h
   MT5_LOGIN=seu_numero_de_conta
   MT5_PASSWORD=sua_senha
   MT5_SERVER=nome_do_servidor_que_aparece_no_login
   ```
6. Rode `iniciar-windows.bat`. Ele instala o pacote `MetaTrader5` automaticamente.

> Na B3 o robô só abre posições entre 10h e 16h50, em dias úteis. Para operar 24h use cripto. Dá para rodar duas cópias do robô em pastas diferentes, uma para cripto e outra para B3, cada uma com o seu `.env` e `PORT` (8000 e 8001).

### 3.4 Bolsa dos EUA e Pesquisa (trader-skill)
1. **SEC (obrigatório para Copycat, Analista e Notícias):** a SEC só pede identificação. No `.env`:
   ```
   SEC_USER_AGENT=Pablo Seu-Sobrenome seu@email.com
   ```
2. **Alpaca (corretora americana com API):** crie uma conta em **alpaca.markets**. A conta **Paper** é gratuita e liberada na hora. Copie as chaves em *Paper Trading → API Keys*:
   ```
   ALPACA_API_KEY=...
   ALPACA_SECRET=...
   ALPACA_PAPER=true
   ```
   Conta real na Alpaca para residentes no Brasil **não é garantida**. O próprio site diz para consultar o suporte sobre o seu país. Sem Alpaca, o módulo EUA funciona em simulação com preços do Yahoo, e a parte de pesquisa (13F, notícias, Congresso, analista) funciona normalmente.

---

## Fase 4: colocar no ar 24 horas (servidor na nuvem)

Seu computador não precisa ficar ligado. Você aluga um servidor.

| Se vai operar | Servidor | Exemplos (confira preços atuais) |
|---|---|---|
| Só cripto e/ou EUA | VPS **Linux** (Ubuntu), 2 GB de RAM | Hetzner, DigitalOcean, Contabo, AWS Lightsail |
| B3 (MetaTrader 5) | VPS **Windows**, 4 GB de RAM | Contabo Windows, AWS Lightsail Windows, VPS da própria corretora |

### 4.0 Hostinger: caminho recomendado (link com HTTPS para computador e celular)

> A hospedagem de sites comum da Hostinger (Premium/Business) **não serve**: ela não roda programas 24h. Você precisa de um plano **VPS** (KVM 1 ou superior).

1. **Contratar a VPS:** no hPanel, vá em **VPS** → escolha o plano → sistema operacional **Ubuntu 24.04**. Defina a senha de root e anote o **IP** da VPS.
2. **Escolher o endereço do painel:**
   - *Com domínio seu na Hostinger:* hPanel → **Domínios** → seu domínio → **DNS / Nameservers** → adicione um registro **A** com nome `robo` apontando para o IP da VPS. O endereço será `robo.seudominio.com.br`.
   - *Sem domínio:* use o nome que a Hostinger dá à VPS (aparece na página da VPS, algo como `srv123456.hstgr.cloud`).
3. **Abrir o terminal da VPS:** na página da VPS, clique em **Terminal do navegador** (ou rode `ssh root@IP` no seu computador).
4. **Enviar o projeto.** Escolha um jeito:
   - *GitHub (recomendado):* suba a pasta para um repositório **privado** e, na VPS, rode `git clone https://github.com/SEU-USUARIO/robo-investimentos.git`.
   - *Arquivo:* no seu computador, rode `scp robo-investimentos.zip root@IP:/root/`. Depois, na VPS: `apt install -y unzip && unzip robo-investimentos.zip`.
5. **Instalar:**
   ```bash
   cd robo-investimentos
   bash deploy/instalar-hostinger.sh
   ```
   O script pergunta o endereço e o seu e-mail de login. Depois ele instala o Docker, configura o firewall, sobe o robô e gera o certificado HTTPS automaticamente.
6. **Acessar:** abra `https://robo.seudominio.com.br` e entre com o seu e-mail e a **senha inicial** (está no arquivo `deploy/env.hostinger`). Na primeira entrada, o sistema obriga você a criar a sua senha.
7. **Instalar no celular como aplicativo:**
   - *Android (Chrome):* menu ⋮ → **Adicionar à tela inicial**.
   - *iPhone (Safari):* botão Compartilhar → **Adicionar à Tela de Início**.

   O robô vira um ícone, como um app.

**Comandos úteis na VPS** (dentro da pasta do projeto):
```bash
docker compose -f docker-compose.prod.yml logs -f robo     # ver o que o robô está fazendo
docker compose -f docker-compose.prod.yml restart robo     # reiniciar depois de mudar o .env
git pull && docker compose -f docker-compose.prod.yml up -d --build   # atualizar o sistema
```

**Esqueceu a senha?** Na VPS, rode `docker compose -f docker-compose.prod.yml exec robo python -m app.reset_senha`. Ele gera uma senha nova, que você troca no próximo acesso.

### 4.1 Outro servidor Linux com Docker (cripto e EUA)
1. Crie o servidor com Ubuntu 24.04 e anote o IP.
2. Entre pelo terminal: `ssh root@IP_DO_SERVIDOR`.
3. Instale o Docker:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
4. Envie o projeto. No seu computador, rode: `scp robo-investimentos.zip root@IP:/root/`. Depois, no servidor:
   ```bash
   apt install -y unzip && unzip robo-investimentos.zip && cd robo-investimentos
   cp .env.example .env && nano .env      # preencha e salve com Ctrl+O, Enter, Ctrl+X
   docker compose up -d --build
   ```
5. O robô reinicia sozinho se cair ou se o servidor reiniciar. Para ver o que está acontecendo, use `docker compose logs -f`.

### 4.2 Acessar o painel com segurança (recomendado: Tailscale)
Não deixe o painel aberto para a internet inteira.
1. Crie uma conta grátis em **tailscale.com**.
2. Instale no servidor:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh && tailscale up
   ```
   Abra o link que aparecer e autorize.
3. Instale o app Tailscale no celular e no computador, entrando com a mesma conta.
4. Acesse `http://NOME-DO-SERVIDOR:8000`. Só os seus aparelhos conseguem abrir.
5. Feche a porta 8000 no firewall público: `ufw allow OpenSSH && ufw enable`.

### 4.3 Servidor Windows (B3)
1. Conecte por **Área de Trabalho Remota** (mstsc) usando o IP, o usuário e a senha do provedor.
2. Instale o Python (Fase 1.1) e o MetaTrader 5, faça login e ligue o Algo Trading.
3. Copie a pasta do robô, preencha o `.env` e rode `iniciar-windows.bat`.
4. Para iniciar junto com o Windows, crie um atalho do `.bat` na pasta `shell:startup` (Win+R → `shell:startup`).

### 4.4 Backup
Tudo fica na pasta `data/` (o banco `robo.db` e os arquivos `trader/`). Copie essa pasta de vez em quando.

---

## Fase 5: passar para dinheiro real (checklist)

**Por que não 30 dias?** Um mês prova que o sistema *funciona* (conexão, ordens, stops, Telegram). Mas não prova que a estratégia *ganha dinheiro*: no gráfico de 1h são só 10 a 30 operações, e normalmente um único tipo de mercado. O resultado pode ser sorte.

O painel tem o cartão **"Pronto para dinheiro real?"** na Visão geral. Ele confere sozinho os 5 critérios abaixo. Só passe para o dinheiro real quando **todos** estiverem ✅:

| Critério | Por quê |
|---|---|
| Pelo menos **60 dias** de simulação | Pegar mais de um tipo de mercado |
| Pelo menos **50 operações** fechadas | Ter amostra suficiente para separar habilidade de sorte |
| O mercado teve uma **queda de 15% ou mais** no período | Ver o robô se defendendo numa queda de verdade |
| Queda do patrimônio dentro do limite e **trava nunca disparou** | Gestão de risco funcionando |
| Lucro positivo e **fator de lucro ≥ 1,1** | Ganhos maiores que as perdas, já descontadas as taxas |

Além do cartão, marque também:

- [ ] Rodei a testnet ou a conta demo (Fase 3) e as ordens reais saíram sem erro
- [ ] Entendo cada número da aba **Estratégias** e da **Visão geral**
- [ ] Testei **Travar**, **Pausar** e a venda manual de uma posição
- [ ] Recebo e aprovo sinais pelo Telegram
- [ ] Chaves de API **sem permissão de saque** e restritas ao IP do servidor
- [ ] Senha forte no painel e acesso só pelo Tailscale
- [ ] Vou começar com um valor que eu aceito perder (sugestão: 5 a 10% do que pretendo usar)

Depois, no `.env`, coloque `TRADING_MODE=live`, `EXCHANGE_TESTNET=false` e `INITIAL_CAPITAL` igual ao valor depositado. Apague a pasta `data/` para zerar a simulação e reinicie. Na aba **Finanças**, registre o aporte.

**Fique no modo MANUAL nas primeiras semanas.** Ligue o automático (Configurações → Modo de execução → digitar CONFIRMO) só quando estiver confortável com o que o robô propõe.

---

## Como usar no dia a dia

**Modo manual (padrão).** O robô manda o sinal e você aprova ou recusa no painel ou no Telegram. Sem resposta, o sinal expira em 60 minutos.

**Modo automático.** O robô executa sozinho, 24h, e avisa no Telegram cada compra e venda.

**Sempre automáticos, em qualquer modo:**
- **Stop de proteção:** fica 2× o ATR abaixo do preço de entrada e sobe junto quando o preço sobe (stop móvel).
- **Alvo de lucro:** vende quando o ganho chega a 3× o risco da operação.
- **Trava de segurança:** com queda de 15% desde o topo, o robô para de comprar até você destravar.

**Evolução.** A cada 24h o robô reavalia as estratégias.
- Com "Ativar sozinho estratégias melhores" em **não**, as novas aparecem como 🕐 *proposta* e você clica em **Ativar**.
- Em **sim**, ele troca sozinho.

**Reinvestimento.** Ligado, o lucro aumenta o tamanho das posições. Desligado, o tamanho fica limitado ao que você aportou.

**Pesquisa EUA.**
- *Copiar investidor:* busque pelo nome (ex.: `buffett`), copie o CIK, escolha quantas posições e o orçamento. As ordens passam pelo trader-risk e viram sinais.
- *Rebalancear:* traz a carteira dos EUA de volta aos pesos-alvo.
- *Impostos (EUA):* mostra os lotes e o ganho ou perda de cada um.
- *Analista + Filtro:* lê os resultados das empresas direto da SEC e filtra pelas regras do arquivo `data/trader/rules.json`.
- *Vigias:* notícias (a cada hora), trades do Congresso (a cada 30 min) e alertas de preço (a cada 5 min) chegam no painel e no Telegram.
- Para usar as ferramentas também como skills do Claude Code, abra a pasta do projeto no Claude Code. As 11 skills estão em `.claude/skills/`.

---

## Impostos (Brasil)

A aba **Finanças** mostra, por mês e por mercado, o lucro realizado, o total vendido e as taxas. Esses são os números de que o contador precisa para o DARF e para a declaração de cripto e de ativos no exterior.

As regras de isenção e as alíquotas mudam. **Confirme com um contador.**

---

## Problemas comuns

| Sintoma | Solução |
|---|---|
| Painel não abre | Veja se o robô está rodando e use a porta certa (`PORT` no `.env`) |
| "Falha ao conectar no MetaTrader 5" | O MT5 precisa estar aberto, logado e com Algo Trading ligado; confira `MT5_SERVER` |
| Binance "Invalid API-key" | A chave da testnet só funciona com `EXCHANGE_TESTNET=true` e a real só com `false`; confira a restrição de IP |
| Nenhum sinal aparece | Normal: as estratégias esperam a configuração certa do mercado. Veja a aba **Registro** |
| Sinal "falhou" | O motivo aparece no próprio sinal (saldo, quantidade mínima, mercado fechado) |
| Copycat/Analista com erro da SEC | Preencha `SEC_USER_AGENT` com nome e e-mail reais |

## Dinheiro real na Foxbit ou no Mercado Bitcoin

1. Crie a conta na corretora, termine a verificação de identidade e deposite por PIX.
2. Crie uma chave de API **só com permissão de negociar (trade)**. Nunca marque saque/retirada.
   Se a corretora pedir IP, use o do servidor: 2.25.166.148.
3. No terminal do servidor: `bash /root/robo-investimentos/deploy/ativar-real.sh`
   (escolha a corretora e cole a chave e o segredo; o segredo não aparece na tela).
4. No painel, a carteira real aparece como "💰 Real (R$)". A simulação continua ao lado.

Travas próprias do dinheiro real: para de comprar se perder a "perda diária máx." no dia e trava
tudo se cair a "queda máx. desde o topo". Para destravar: Ajustes > destravar (digite CONFIRMO).
Para desligar o dinheiro real: apague as linhas REAL_ do arquivo /opt/robo/.env e reinicie (`systemctl restart robo`).
