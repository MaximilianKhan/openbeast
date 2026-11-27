# Install Python packages on a Spark, online or offline

If pip reports `externally-managed-environment`, create a virtual environment for your own scripts. The Sparks already have Python 3.12 and the system venv package. Run these commands in a shell on the machine where the Python script will run. A client calling a model over HTTP only needs its Python libraries on the client.

## Create once, reuse later

```sh
python3 -m venv ~/.venvs/spark-tools
source ~/.venvs/spark-tools/bin/activate
python -m pip --version
```

The printed path should be inside `.venvs/spark-tools`. In a new shell, repeat only the activation line. For scheduled jobs, invoke `~/.venvs/spark-tools/bin/python` directly. This works without sudo or changes to the model server.

## While online, save packages for later

A wheelhouse is an ordinary folder of installable Python packages. This example saves Requests **and its dependencies**. Substitute the package your script needs, including a version pin if needed. Prepare the folder on a Spark or a matching Linux ARM64 system with the same Python version.

```sh
source ~/.venvs/spark-tools/bin/activate     # the venv above, so `python` is its 3.12
WHEELS="$HOME/wheelhouses/spark-tools"
mkdir -p "$WHEELS"
python -m pip download --only-binary=:all: --dest "$WHEELS" requests
```

If pip reports that no compatible wheel exists, build it while connected with `python -m pip wheel --wheel-dir "$WHEELS" PACKAGE` on a matching system with the required build tools. Some packages also need system libraries at runtime. Test in a fresh venv before disconnecting. Copy the complete wheelhouse to any other Spark or removable drive that will need it.

## Once offline, install from the saved folder

```sh
source ~/.venvs/spark-tools/bin/activate
python -m pip install --no-index --find-links="$HOME/wheelhouses/spark-tools" requests
python -m pip check
python -c 'import requests; print(requests.__version__)'
```

If offline installation reports “No matching distribution,” check the named package or dependency, Python version and wheel architecture. Bring any missing compatible wheels over LAN or removable storage. An already installed package in the same venv remains usable offline.

For OpenBeast's own pinned dependencies, use the repository's [offline installation instructions](INSTALL.md#9-offline--air-gapped-install) and `scripts/pydeps.sh wheelhouse`.
