# SharpCap Focus Sequencer

`sharpcap-focus-sequencer` aplica corrección térmica de foco bajo demanda a enfocadores ZWO EAF mediante ASCOM. Lee el modelo térmico producido por el proyecto hermano `sharpcap-focus-temperature`, actualiza ese modelo desde los logs de SharpCap antes de una ejecución normal, consulta la temperatura del EAF y mueve el enfocador a la posición compensada.

Soporta dos trenes ópticos independientes:

| Tubo | Equipo | Offsets de filtro | Log diario |
|---|---|---:|---|
| `main` | Celestron C8 con reductor f/6.3, ASI2600MC Pro y ZWO EAF | Sí | `logs\YYYYMMDD_focus_sequencer.log` |
| `guide` | Sky-Watcher 50ED, ASI224MC y segundo ZWO EAF | No; siempre cero | `logs\YYYYMMDD_focus_sequencer_guide.log` |

## Funcionamiento

En cada ejecución, el secuenciador:

1. Selecciona el tren óptico con `--tube main` o `--tube guide`.
2. Carga el JSON de estado de foco correspondiente, generado por `sharpcap-focus-temperature`.
3. Se conecta al enfocador ASCOM seleccionado y termina de forma segura si el enfocador ya está en movimiento.
4. Actualiza el JSON de estado a partir de logs recientes de SharpCap, salvo cuando se usa `--dry-run`.
5. Calcula el objetivo térmico:

   ```text
   base_target = focus_ref + TCF × (current_temperature - reference_temperature)
   final_target = base_target + filter_offset
   ```

6. Aplica compensación de backlash cuando el objetivo está por debajo de la posición actual.
7. Registra `START`, estado de actualización, detalles de la corrección y `END` en el log diario específico del tubo.

## Requisitos

- Windows con ASCOM Platform y un ZWO EAF accesible mediante ASCOM.
- Se recomienda Python 3.10 o superior.
- Un clon hermano de `sharpcap-focus-temperature` junto a este repositorio:

  ```text
  C:\astro\sharpcap-focus-sequencer
  C:\astro\sharpcap-focus-temperature
  ```

- Un archivo de estado térmico válido para cada tubo:

  ```text
  C:\astro\sharpcap-focus-temperature\sharpcap_focus_state.json
  C:\astro\sharpcap-focus-temperature\sharpcap_focus_state_guide.json
  ```

- Se admiten entornos virtuales separados. El sequencer ejecuta el productor usando preferentemente:

  ```text
  C:\astro\sharpcap-focus-temperature\.venv\Scripts\python.exe
  ```

  Ese entorno debe incluir las dependencias del productor, entre ellas `numpy` y `matplotlib` cuando sean necesarias para `sharpcap_focuser.py`.

## Instalación

Clona ambos proyectos como repositorios hermanos bajo un directorio común. Crea sus entornos virtuales de forma independiente e instala los requisitos de cada proyecto en su entorno correspondiente.

```cmd
cd C:\astro\sharpcap-focus-sequencer
py -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

cd C:\astro\sharpcap-focus-temperature
py -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copia `focus_sequencer.properties.example` como `focus_sequencer.properties` y configura los offsets de filtro del tubo principal. La posición 1 debe ser el filtro de referencia sin filtro y su offset debe ser cero.

## Lanzadores

Usa los archivos BAT proporcionados desde pasos de comando externo de SharpCap o desde una consola.

| Lanzador | Comando enviado a Python | Tubo |
|---|---|---|
| `run_focus.bat` | `focus_sequencer.py --tube main` | Tren principal de captura |
| `run_focus_guide.bat` | `focus_sequencer.py --tube guide --state-json ..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json` | Tren guía |

Los dos lanzadores reenvían argumentos adicionales con `%*`. Por ejemplo, para realizar pruebas sin movimiento:

```cmd
run_focus.bat --dry-run
run_focus_guide.bat --dry-run
```

El lanzador de guiado selecciona explícitamente `sharpcap_focus_state_guide.json`. El lanzador principal usa por defecto `sharpcap_focus_state.json`.

## Uso por línea de comandos

### Tubo principal

```cmd
.venv\Scripts\python.exe focus_sequencer.py --tube main
.venv\Scripts\python.exe focus_sequencer.py --tube main --filter 2
.venv\Scripts\python.exe focus_sequencer.py --tube main --filter 2 --dry-run
.venv\Scripts\python.exe focus_sequencer.py --tube main --config custom.properties
```

### Tubo guía

```cmd
.venv\Scripts\python.exe focus_sequencer.py --tube guide
.venv\Scripts\python.exe focus_sequencer.py --tube guide --dry-run
.venv\Scripts\python.exe focus_sequencer.py --tube guide --state-json "C:\astro\sharpcap-focus-temperature\sharpcap_focus_state_guide.json"
```

El tubo guía utiliza siempre offset de filtro cero. `--filter` y `--config` solo se permiten con `--tube main` y se rechazan para `--tube guide`.

## Logs

El nombre del log diario se deriva del tubo seleccionado:

```text
--tube main   -> logs\YYYYMMDD_focus_sequencer.log
--tube guide  -> logs\YYYYMMDD_focus_sequencer_guide.log
```

Cada ciclo de corrección registra la referencia del modelo, temperatura, delta térmico, coeficiente térmico, contexto de filtro, objetivo calculado, posición inicial, corrección solicitada, uso de backlash, posición final y motivo de terminación.

Ejemplo:

```text
2026-09-27 03:18:24 | START | tube=main | ref=... | focus_ref=... | T_ref=... | TCF=...
2026-09-27 03:18:29 | INFO  | tube=main | T=... | dT=... | target=... | pos=... | correction=... | backlash=False | final=...
2026-09-27 03:18:29 | INFO  | END   | pos=... | reason=ok
```

Los motivos de terminación posibles incluyen:

```text
ok
ok_with_warning
min_correction
busy
dry_run
error
configuration_error
```

## Filtros del tubo principal

El modelo térmico del tren principal se calibra en la posición 1 de la rueda, `No filter`. El sequencer calcula primero el objetivo térmico sin filtro y luego aplica el offset del filtro seleccionado:

```text
base_focus_target = focus_ref + TCF × (T_current - T_ref)
final_focus_target = base_focus_target + filter_offset_steps
```

Mantén:

```properties
filter.1.offset_steps=0
```

Configura los filtros de captura y sus offsets en `focus_sequencer.properties`.

## Comportamiento de seguridad

- Si el enfocador informa `IsMoving=True`, el sequencer salta ese ciclo para no competir con un autofocus de SharpCap.
- Una corrección que necesita overshoot de backlash se omite si su magnitud absoluta es inferior a `--min-correction`.
- Las correcciones que no requieren backlash se aplican incluso si son pequeñas, para evitar acumular deriva térmica.
- El objetivo se limita a los límites ASCOM del enfocador cuando el driver los proporciona.
- `--dry-run` lee estado, posición y temperatura, pero no actualiza el JSON de estado ni mueve el enfocador.

## Solución de problemas

### `ModuleNotFoundError` para `numpy` o `matplotlib`

`sharpcap_focuser.py` pertenece a `sharpcap-focus-temperature` y debe ejecutarse con el entorno virtual de ese proyecto. Verifica el intérprete y las dependencias:

```cmd
C:\astro\sharpcap-focus-temperature\.venv\Scripts\python.exe -c "import sys, numpy, matplotlib; print(sys.executable); print('OK:', numpy.__version__, matplotlib.__version__)"
```

Si el entorno no existe o está incompleto, recréalo desde el repositorio productor:

```cmd
cd C:\astro\sharpcap-focus-temperature
py -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### `UPDATE FAILED`

Esto significa que la actualización del estado térmico mediante el productor no finalizó correctamente. Revisa la línea `UPDATE FAILED` del log específico del tubo. El sequencer puede continuar con el último estado válido almacenado en el JSON, pero el modelo podría estar desactualizado.

### No se puede escribir `sharpcap_final_focus.csv`

No configures el productor para escribir archivos generados bajo:

```text
C:\Program Files\SharpCap 4.1 (64 bit)
```

Usa un directorio de datos escribible, por ejemplo `C:\astro` o el directorio del proyecto productor.

### Verificar los dos logs

Después de ejecutar ambos lanzadores con `--dry-run`, confirma que existen ambos ficheros:

```cmd
dir logs\*_focus_sequencer.log
dir logs\*_focus_sequencer_guide.log
```

El log principal debe contener `tube=main` y el de guía debe contener `tube=guide`.

## Integración con SharpCap Sequence Analyzer

Los logs separados permiten que `sharpcap-sequence-analyzer` asocie con precisión las correcciones térmicas:

| Comando de SharpCap | Log de Focus Sequencer |
|---|---|
| Comando de enfoque del tubo principal | `*_focus_sequencer.log` |
| Comando de enfoque del tubo guía | `*_focus_sequencer_guide.log` |

El analizador podrá informar por separado, para el C8 y el ED50, de la temperatura, TCF, corrección solicitada, backlash, posiciones antes y después, y motivo de terminación.

## Licencia

GPL-3.0-or-later. Consulta `LICENSE.txt`.