# Vault — Member 3 recovery package

Runnable Python prototype for **health monitoring, integrity verification, replica repair, rebalancing, failure simulation, monitoring APIs and tests**.

Start with `recovery/README.md` for the full guide. Give `CONTRACT.md` to your backend teammate. Read `VALIDATION.md` before describing the project's guarantees in your presentation.

## Start in three commands

Run from this extracted `vault_member3` directory, using Python 3.11 or newer (tested with 3.12):

```sh
python -m pip install -r recovery/requirements.txt
python -m pytest -q
python -m uvicorn recovery.api:create_app --factory --host 127.0.0.1 --port 8003
```

Open [interactive API documentation](http://127.0.0.1:8003/docs). The default demo creates four simulated nodes and a sample object, `file-123`, with three replicas. It does not start four independent storage-server processes.

In another terminal:

```sh
python -m recovery.failure_injector --node node-3 --action stop
```

Watch [node status](http://127.0.0.1:8003/nodes) and [repair status](http://127.0.0.1:8003/repair-status). For deterministic demonstrations without waiting for background intervals:

```sh
python -m recovery.demo.demo_failure_recovery
python -m recovery.demo.demo_corruption_repair
python -m recovery.demo.demo_rebalancing
```

**Integration is not finished simply by running demo mode.** No existing backend source was supplied. An HTTP adapter is included, but your actual coordinator/storage services must implement the conditional, versioned contract in `CONTRACT.md`. Do not remove these safety checks merely to match simpler endpoints.

There is no frontend or production storage engine in this package. All simulated data is disposable. Use only one recovery process/worker.
