FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py bot.py world.py grid.bin exposure.bin ./
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
