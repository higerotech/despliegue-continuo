# ADR-0011: Un reconciliador recupera los despliegues cuyo webhook no llegó

* **Estado:** accepted
* **Fecha:** 2026-10-23
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0011
* **Supersede / Superseded-by:** complementa a ADR-0002 (el webhook sigue siendo el disparador) y a ADR-0008 (la cola)
* **Controles OWASP afectados:** A04 (diseño inseguro), A08 (integridad de software y datos), A09 (registro y monitorización)

## Contexto

Origina: RF01. El sistema se diseñó suponiendo que **GitHub reintenta las entregas fallidas**.
El docstring de `DeliveryCache` lo afirmaba y el contrato de errores justificaba el `202 ignored`
con lo mismo. Es falso: GitHub marca la entrega como fallida en *Recent Deliveries* y no
vuelve a enviarla. Solo se reenvía si alguien pulsa *Redeliver*.

El 2026-10-23, entre las 15:26 y las 15:41 UTC, fallaron por timeout 19 entregas de
`higerotech/morocota` (`context deadline exceeded`, sin cabeceras en 10 s). En esa ventana
`wan2` entró y salió del balanceo de midgard una docena de veces. Una de esas entregas era el
`completed` de `build-api` para `d365c76`. El build había terminado bien, pero nunca se
desplegó y nadie se enteró. Lo detectó, horas después, una persona que comparaba versiones a mano.

Además de la red, otras dos vías pierden eventos:

- el reinicio diario de `needrestart`, que cancela lo que esté en cola (los trabajos viven en
  memoria);
- cualquier corte del túnel o del receptor mientras GitHub entrega.

## Decisión

**Un reconciliador dentro del propio receptor consulta cada `RECONCILE_INTERVAL` segundos el
último run correcto del workflow de cada app y encola lo que falte.** El webhook sigue siendo el
disparador normal. El reconciliador solo cubre los eventos que se perdieron.

La invariante es una sola: **solo se recupera lo que el receptor no vio; nunca se repite una
decisión ya tomada.** De ella salen las reglas, que se aplican en orden y decide la primera que
encaja:

1. Se consulta `GET /repos/{repo}/actions/runs?branch=…&status=success`, una sola petición por
   cada par repo y rama. De ahí se toma el run más reciente del workflow de la app. **Ese run
   pasa por el mismo `parse_event` y `match_app` que un webhook**, y además se descartan
   `pull_request` y los runs de forks. El reconciliador nunca acepta algo que el webhook habría
   rechazado.
2. Si ya corre `tag_for(sha)` → **al día**.
3. Si el sha aparece en el histórico `/var/log/cd-receiver/<app>.jsonl`, con cualquier
   resultado → **no se toca**. Un fallo con rollback ya se decidió y se avisó: reintentarlo en
   cada ciclo sería un bucle de rollbacks.
4. Si el sha está en cola o en curso → no se toca.
5. Si el estado de la app (`updated_at`) es **posterior** al final del run → no se toca. Alguien
   desplegó o alineó a mano después del evento, y esa decisión es más reciente.
6. Si el run terminó hace menos de `RECONCILE_GRACE` segundos → se espera: se le da margen al
   webhook.
7. En cualquier otro caso → **se encola**, con `delivery_id = reconciliacion-run-<id>`, que deja
   constancia en el histórico de que lo recuperó el reconciliador. Se registra un `WARNING`
   «entrega perdida».

**Opt-in y con modo observar.** `RECONCILE_INTERVAL=0` (el defecto) lo desactiva, igual que
`NOTIFY_URL` vacía desactiva los avisos: el receptor sigue funcionando sin depender de un
servicio externo. Con `RECONCILE_DRY_RUN=true` decide y registra, pero no encola. Así se
introduce en un servidor nuevo antes de dejarlo actuar.

**Token opcional, de solo lectura.** Sin token, la API pública basta para los repos públicos,
con un límite de 60 consultas por hora. Un repo privado responde 404. `RECONCILE_TOKEN` admite
un token *fine-grained* con un único permiso: **Actions: read** sobre los repos del inventario.
Va en `receiver.env` (`0600 root:root`) y no aparece en `/status` ni en el log.

**`/status` publica la última pasada** (`reconciliacion`: modo, decisiones app por app y errores
por repo). Sin entrar al journal, el operador ve por qué una app no se recuperó.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo |
|---|---|---|---|
| **Reconciliador por consulta dentro del receptor (elegida)** | No depende de que el evento exista; cubre la red, el túnel y los reinicios; reutiliza la cola, el estado y el filtro | Una dependencia saliente nueva (`api.github.com`) y, para repos privados, un token | Bajo: solo lectura, mismo filtro que el webhook, opt-in |
| Reenviar las entregas fallidas por la API de *hooks* | Repite el camino exacto del webhook | Exige un token con permiso de **administración** sobre los webhooks del repo; no cubre lo que se cayó de la cola en memoria | Medio: un token capaz de reescribir el webhook |
| Persistir la cola en disco | Cubre los reinicios | No cubre lo que nunca llegó, que es el incidente | — (no resuelve el problema) |
| Cron externo en GitHub Actions que verifique el despliegue | Punto de vista exterior | Los cron de Actions llegaron con 5–8 h de retraso en otro repo de la misma cuenta; exigiría publicar `/status` | Medio |
| Sacar el túnel de la WAN inestable | Ataca la causa de este incidente | Es un arreglo de red, no del contrato: el próximo corte será otro | — (complementario, va en Yggdrasil) |

## Consecuencias

- Positivas: un build correcto acaba desplegado aunque su webhook se pierda, con un retraso
  máximo de `RECONCILE_INTERVAL + RECONCILE_GRACE`. Con la configuración de midgard (300 s y
  120 s) son unos 7 minutos.
- Positivas: el `WARNING` «entrega perdida» y el `delivery_id` `reconciliacion-run-*` hacen
  visible una pérdida que antes era silenciosa.
- Negativas / deuda asumida: solo se recupera **el último** run correcto de cada app. Si se
  perdieron dos seguidos, se despliega el más reciente, que es lo que haría un webhook.
- Negativas / deuda asumida: si el workflow de una app no aparece entre los últimos 100 runs
  correctos de su rama, no se concluye nada. Una pérdida es, por definición, reciente, así que
  basta.
- Negativas / deuda asumida: un repo con apps sin `workflow`, o con `event: push`, no se
  reconcilia. Aceptar cualquier workflow correcto del repo desplegaría con el primero que pasara.
- Negativas / deuda asumida: una app recién añadida al inventario, sin estado ni histórico, se
  despliega en el primer ciclo con su último build. Es lo mismo que haría el próximo webhook.
  Conviene darla de alta cuando su `project_dir` ya esté listo.
- Corrección documental: el docstring de `DeliveryCache` y el contrato de errores decían que
  GitHub reintenta. Ya no lo dicen.
- Impacto en threat model: **T14** nueva (abuso del token o suplantación de la API). La amenaza
  de disponibilidad del túnel pasa de «sin despliegues» a «despliegues retrasados». **T6** no
  cambia: se exige el mismo `conclusion: success` y el mismo workflow.

## Verificación

- `tests/test_reconcile.py`: una prueba por regla. Despliega lo perdido; no toca lo que está al
  día, lo ya intentado (incluido un rollback), lo que está en cola, lo alineado a mano después
  del run ni lo recién terminado. Descarta otro workflow, una build fallida, otra rama, un
  `pull_request` o un fork. En un monorepo, cada app toma su run. Un 404, una red caída o un
  ciclo que revienta no rompen nada. El token no aparece en `/status`.
- `tests/test_admin.py::TestReconciliacionEnStatus`: `/status` publica el estado del
  reconciliador, que arranca y se apaga con el servicio.
- Simulación con los datos reales de midgard y de GitHub del 2026-10-23: con el estado
  vigente, ninguna decisión de desplegar (7 apps al día y 4 sin runs recientes). Con el estado
  previo a la alineación manual, a las 15:45 recupera exactamente el run `39978352894`
  (`build-api`, `d365c76`) y deja quieto `morocota-worker`.
