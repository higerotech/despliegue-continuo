# ADR-0005: Socket-proxy con API recortada en lugar del grupo `docker`

* **Estado:** accepted
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0005
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A01 (control de acceso), A06 (diseño inseguro), A02 (configuración de seguridad)

## Contexto

Origina: RS04. El receptor necesita hablar con Docker para desplegar. La forma habitual es
añadir su usuario al grupo `docker`, y esa es la primera versión que se implementó.

**Pertenecer al grupo `docker` equivale a ser root en el host.** No es una opinión: con acceso
al socket cualquiera puede lanzar `docker run -v /:/host --privileged` y escribir en el sistema
de ficheros completo. El receptor es un proceso expuesto —indirectamente— a Internet, así que
un fallo en él se convertía en compromiso total de la máquina.

## Decisión

Se interpone **`tecnativa/docker-socket-proxy`** entre el receptor y el daemon. El proxy es el
único contenedor que monta `/var/run/docker.sock` (además en `:ro`), y publica una API de
Docker recortada en `127.0.0.1:2375`. El receptor la consume vía `DOCKER_HOST`
(`deployer._run`), y su usuario de servicio **no pertenece al grupo `docker`** — el instalador
lo retira activamente si lo encuentra, tratándolo como regresión.

Superficie concedida, la mínima que `docker compose` necesita para desplegar: `PING`,
`VERSION`, `INFO`, `CONTAINERS`, `IMAGES`, `NETWORKS`, `VOLUMES`, `DISTRIBUTION`, `POST` y los
permisos de arranque/parada/reinicio.

Denegado explícitamente: `EXEC` (sin shells en contenedores ajenos), `SECRETS`, `SWARM`,
`NODES`, `SERVICES`, `TASKS`, `CONFIGS`, `PLUGINS`, `SESSION`, `SYSTEM` (sin `prune` remoto),
`BUILD` (el build vive en Actions, nunca en el servidor), `COMMIT`, `AUTH` y `EVENTS`.

### Verificación realizada

Comprobado contra Docker 29.5.2 antes de aceptar la decisión, no asumido:

| Comprobación | Resultado |
|---|---|
| `docker compose pull` a través del proxy | Funciona |
| `docker compose up -d` a través del proxy | Funciona; la aplicación responde `HTTP 200` |
| Recreado con otro tag (ruta de rollback) | Funciona; `docker inspect` confirma la imagen anterior |
| `docker exec` | **Bloqueado** — `403` |
| `docker system df` | **Bloqueado** — `403` |

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo residual |
|---|---|---|---|
| **Socket-proxy con API recortada (elegida)** | Quita el grupo `docker`; bloquea `exec`/`secrets`/`swarm`; punto único de auditoría; sin cambios en las aplicaciones | Una pieza más que mantener; el proxy sí es root-equivalente | **Sigue siendo equivalente a root** (ver abajo) |
| Grupo `docker` directo | Cero piezas | Root-equivalente sin ninguna barrera ni auditoría | Máximo |
| Docker rootless | **Elimina** la equivalencia a root | Rehace el despliegue de todas las aplicaciones; limita puertos < 1024, red y volúmenes | Bajo |
| `sudo` con reglas para comandos concretos | Sencillo de auditar | `sudo docker` sigue siendo root; los comodines en `sudoers` se evaden con facilidad | Máximo |

## Consecuencias

- Positivas: RS04 mejora de forma medible. El usuario `deploy` ya no está en el grupo `docker`;
  `exec` y `system` están cerrados; existe un punto único donde registrar y limitar el acceso a
  la API. Los contenedores desplegados no cambian en nada.

- **Limitación explícita, y es importante no exagerar la mitigación:** conceder `POST` +
  `CONTAINERS` es imprescindible para desplegar, y con ellos **todavía se puede crear un
  contenedor privilegiado o con `/` montado**. Es decir: el socket-proxy **reduce** la
  superficie y añade auditoría, pero **no elimina la equivalencia a root**. Quien comprometa el
  receptor sigue pudiendo comprometer el host, solo que por un camino más estrecho y más
  ruidoso.

  La única mitigación que cierra realmente ese vector es **Docker rootless**, registrada como
  evolución en el threat model. Documentar el socket-proxy como si eliminase el riesgo sería
  precisamente el tipo de control no fundamentado que el proceso pretende evitar.

- Negativas / deuda asumida: el proxy es un contenedor más en el arranque. Si no está en pie,
  los despliegues fallan con un error de conexión claro. Corre con `restart: unless-stopped`,
  de modo que Docker lo levanta al arrancar el host; no lleva unidad de systemd propia.

- Condición de revisión: si el receptor pasara a desplegar aplicaciones de terceros, o si
  apareciera una segunda superficie de entrada, **rootless deja de ser evolución y pasa a ser
  requisito**.

- Impacto en threat model: **T4** baja de *crítica* a *alta* — reduce probabilidad y estrecha
  el camino, pero el impacto sigue siendo total.
