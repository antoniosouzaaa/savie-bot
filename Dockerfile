# Dockerfile

# Usamos uma imagem base leve do Python
FROM python:3.11-slim

# Definimos o diretório de trabalho dentro do container
WORKDIR /app

# Copiamos o arquivo de dependências primeiro
COPY requirements.txt .

# Instalamos as dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos o resto do código do seu bot para o container
COPY . .

# Comando que será executado quando o container iniciar
# Se seu arquivo python tiver outro nome, mude aqui.
CMD ["python", "savie_bot.py"]