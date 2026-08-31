# Arquitectura — dónde está ahora

Este documento fue el resumen de ingeniería del sistema antes de adoptar AI-DLC. Su contenido
está ahora en la documentación por fases, y **este fichero se ha vaciado a propósito**: mantener
dos descripciones de la misma arquitectura produjo deriva en el primer cambio de diseño (decía
que el receptor pertenecía al grupo `docker`, algo que dejó de ser cierto con
[ADR-0005](00-project/adr/0005-socket-proxy-en-lugar-de-grupo-docker.md)).

Regla que se sigue a partir de aquí: **derivar, no duplicar**.

| Buscas | Está en |
|---|---|
| Qué problema resuelve y qué queda fuera | [`00-project/charter.md`](00-project/charter.md) |
| Vocabulario del dominio | [`00-project/glossary.md`](00-project/glossary.md) |
| Qué datos hay y cómo se protegen | [`00-project/data-classification.md`](00-project/data-classification.md) |
| Requisitos y escenarios de abuso | [`01-requirements/despliegue-continuo-webhook.md`](01-requirements/despliegue-continuo-webhook.md) |
| **Diagramas C4, secuencia, estados y datos** | [`02-design/architecture.md`](02-design/architecture.md) |
| **Amenazas, STRIDE y riesgo residual** | [`02-design/threat-model.md`](02-design/threat-model.md) |
| Contrato HTTP y esquema de `apps.yml` | [`02-design/interfaces-contract.md`](02-design/interfaces-contract.md) |
| **Por qué cada decisión** | [`00-project/adr/`](00-project/adr/) — ADR-0001 a ADR-0008 |
| Instalar, operar y diagnosticar | [`03-implementation/deployment-runbook.md`](03-implementation/deployment-runbook.md) |
| Historial real del repositorio | [`03-implementation/repo-history.md`](03-implementation/repo-history.md) |
| Qué está probado y qué no | [`04-testing/test-strategy.md`](04-testing/test-strategy.md) |
| Estado de cada gate | [`../.ai-dlc/gates/`](../.ai-dlc/gates/) |
