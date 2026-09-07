# ADR-0009: Aviso del resultado por webhook genérico, y solo de lo que sale mal

* **Estado:** accepted
* **Fecha:** 2026-09-07
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0009
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A09 (fallos de registro y monitorización)

## Contexto

Origina: deuda **DS-03**. El receptor responde a GitHub **antes** de desplegar (ADR-0008), así
que el resultado no viaja de vuelta: en *Recent Deliveries* la entrega figura correcta aunque
el despliegue haya fallado y revertido.

Mientras solo existía el canario —que redespliega una imagen fija y siempre funciona— la deuda
era teórica. Con tres aplicaciones reales en `hgtech001` deja de serlo: **un rollback de
madrugada solo se descubre mirando `/status`**, y nadie mira `/status` de madrugada.

## Decisión

Tres decisiones acopladas.

### 1. El destino es un webhook genérico, no una integración concreta

`NOTIFY_URL` recibe un `POST` con JSON. El cuerpo lleva `text` y `content` además de los campos
estructurados, que es lo que esperan Slack, Discord, ntfy y la mayoría de relays: funciona con
todos ellos **sin escribir un adaptador por destino**.

### 2. Por defecto solo se avisa de lo que sale mal

`NOTIFY_ON=failure` (defecto) notifica fallos y rollbacks; `always` incluye los correctos.

Un canal que suena en cada despliegue correcto se acaba silenciando, y entonces tampoco suena
cuando importa. El valor de una alerta es inversamente proporcional a su frecuencia.

Un rollback **sí** se notifica aunque el servicio quede en pie: el despliegue no entró, y eso es
información que el operador necesita.

### 3. Un fallo al notificar nunca afecta al despliegue

El aviso es observabilidad, no parte de la operación. `Notificador.enviar` no propaga
excepciones, y `DeployQueue._avisar` **vuelve a aislarlas de forma explícita** en lugar de
confiar en el `except` general del worker.

Esa redundancia es deliberada, y viene de la lección de **T13**: una propiedad de seguridad
sostenida por accidente se rompe en silencio. Si un canal de avisos roto pudiera tumbar el
worker, esa aplicación dejaría de desplegarse sin que nadie lo notara — exactamente lo contrario
de lo que esta ADR pretende.

El aviso se envía **después** de registrar el resultado en el histórico y en el `.jsonl`: la
fuente de verdad no puede depender de que el canal responda.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo |
|---|---|---|---|
| **Webhook genérico (elegida)** | Un solo camino de código; funciona con Slack, Discord, ntfy o un relay propio; sin credenciales por destino | El operador monta su propio canal | Ninguno relevante: está aislado |
| **API de Deployments de GitHub** | El estado quedaría junto al commit, visible en el repositorio | Exige un token con `deployments: write` **por cada repositorio desplegable**, y renovarlo. Más superficie y más secretos en el servidor | Un token con escritura en varios repos es un objetivo apetecible |
| **Integración nativa con Slack** | Formato más rico | Acopla el receptor a un proveedor; inútil para quien no use Slack | Lock-in gratuito |
| **Correo SMTP** | Sin servicios externos | Credenciales SMTP en el servidor, entregabilidad dudosa, ruido en la bandeja | Medio |
| **Métricas a la instancia de SigNoz existente** | Reaprovecha infraestructura ya desplegada | OTLP sirve para telemetría continua, no para avisar de un evento puntual; necesitaría además reglas de alerta | Complejidad desproporcionada |

La API de Deployments era la alternativa seria y se descartó por el coste en secretos: obligaría
a mantener un token con permiso de escritura por repositorio en el mismo servidor que ya guarda
el secreto HMAC. **Concentrar credenciales de escritura sobre los repositorios en el host que
despliega empeora el impacto de T4** (escalada a root), que ya es la amenaza alta aceptada.

## Consecuencias

- Positivas: **DS-03 cerrada**. Un despliegue fallido o revertido llega a un canal humano en
  segundos. El cuerpo incluye `previous_tag`, de modo que el aviso dice **a qué versión volvió**
  el servicio y no solo que hubo rollback.
- Nuevo campo `previous_tag` en `DeployResult`, que aparece también en `/status` y en el
  `.jsonl`. Cambio aditivo: no rompe consumidores existentes.
- Negativas / deuda asumida: el canal es responsabilidad del operador. Si `NOTIFY_URL` apunta a
  un servicio muerto, los avisos se pierden con un `warning` en el log y **nada más lo delata**;
  no hay reintento ni cola de avisos pendientes. Es aceptable porque el `.jsonl` sigue siendo la
  fuente de verdad auditable.
- La URL puede llevar un token en la ruta (lo habitual en Slack y Discord). Vive en
  `receiver.env`, con los mismos permisos `0600 root:root` que el secreto del webhook.
- Impacto en threat model: mitiga la parte de **A09** que DS-03 dejaba abierta. No introduce
  superficie de entrada: es tráfico saliente, y el receptor no expone nada nuevo.
