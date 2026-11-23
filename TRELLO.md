# Quadro do Trello: Robô de Investimentos

**Como montar o quadro rápido:** crie no Trello uma lista com cada título abaixo. Depois copie todas as linhas daquela seção (sem os hífens) e cole em "Adicionar um cartão". O Trello pergunta se quer criar **um cartão por linha**: confirme.

Os detalhes de cada cartão estão no GUIA.md, na fase indicada.

---

## Fase 1 · Testar no computador
- Instalar Python 3.12 (marcar "Add to PATH")
- Extrair o zip e criar o arquivo .env a partir do .env.example
- Trocar DASHBOARD_PASSWORD no .env
- Rodar com BROKER=synthetic e abrir http://localhost:8000
- Aprovar e recusar sinais de teste no painel
- Testar os botões Pausar e Travar

## Fase 2 · Cripto em simulação (dados reais)
- Mudar .env para BROKER=crypto e TRADING_MODE=paper
- Apagar a pasta data/ e religar o robô
- Conferir as estratégias escolhidas na aba Estratégias
- Acompanhar os sinais por 1 semana no modo manual

## Fase 3 · Contas
- Telegram: criar bot no @BotFather e pegar o token
- Telegram: pegar o chat id e colocar no .env
- Telegram: testar /status, /pendentes e aprovação pelo botão
- Binance: criar chaves na testnet.binance.vision
- Binance: rodar com TRADING_MODE=live e EXCHANGE_TESTNET=true
- Binance: abrir conta real + KYC + 2FA (só criar, não depositar)
- B3: escolher corretora com MetaTrader 5 (Genial, Clear ou Rico)
- B3: abrir conta demo do MT5 e ligar o Algo Trading
- B3: rodar o robô no Windows com BROKER=mt5 na conta demo
- EUA: preencher SEC_USER_AGENT no .env
- EUA: criar conta Paper na Alpaca e colocar as chaves no .env
- EUA: testar Copiar investidor (ex.: Berkshire, top 5)
- EUA: perguntar ao suporte da Alpaca se aceita residente no Brasil

## Fase 4 · Hostinger 24h (computador + celular)
- Contratar VPS Hostinger (KVM 1+) com Ubuntu 24.04
- Criar registro DNS A "robo" apontando para o IP da VPS (ou usar o srvXXXX.hstgr.cloud)
- Subir o projeto para um repositório privado no GitHub
- Na VPS: git clone + bash deploy/instalar-hostinger.sh
- Abrir https://robo.seudominio e trocar a senha inicial
- Instalar o painel como app no celular (Adicionar à tela inicial)
- Preencher o Telegram no .env da VPS e reiniciar
- Rotina de backup da pasta data/ da VPS
- (B3) Se for operar B3: contratar VPS Windows à parte para o MetaTrader 5

## Fase 5 · Validação (60 dias ou mais)
- Cartão "Pronto para dinheiro real?": 60 dias de simulação
- Cartão "Pronto para dinheiro real?": 50 operações fechadas
- Cartão "Pronto para dinheiro real?": mercado teve queda de 15% ou mais
- Cartão "Pronto para dinheiro real?": queda do patrimônio dentro do limite
- Cartão "Pronto para dinheiro real?": lucro positivo e fator de lucro ≥ 1,1
- Revisar semanalmente as abas Estratégias e Registro

## Fase 6 · Dinheiro real (só meu)
- Criar chaves reais da Binance SEM permissão de saque e com restrição de IP
- Mudar .env: TRADING_MODE=live, EXCHANGE_TESTNET=false
- Apagar data/ e registrar o aporte na aba Finanças
- Depositar 5 a 10% do valor pretendido
- Operar no modo MANUAL nas primeiras semanas
- Decidir se liga o modo automático
- Aumentar o capital aos poucos conforme o resultado real

## Rotina mensal
- Exportar o resultado mensal da aba Finanças para o contador
- Conferir se as estratégias ativas mudaram e por quê
- Backup da pasta data/

## Futuro · Abrir para terceiros (NÃO fazer agora)
- Consultar advogado de mercado de capitais
- Avaliar registro na CVM como gestor (Resolução CVM 21) ou parceria com gestora registrada
- Alternativa: vender o sistema como software (cada cliente opera a própria conta)
- Adaptar o sistema para várias contas e usuários (login por cliente, carteiras separadas)
