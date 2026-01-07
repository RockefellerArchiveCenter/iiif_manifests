FROM python:3.12-alpine AS base

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache \
    zlib-dev \
    jpeg-dev \
    gcc \
    musl-dev

WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src src

FROM base AS test
COPY .coveragerc ./
COPY tests tests
RUN pip install -r tests/test_requirements.txt

FROM base AS build
CMD [ "python", "-m", "src.create_derivatives" ]