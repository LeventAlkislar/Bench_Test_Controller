# SessionStateMachine Durum Geçişleri

Kaynak: `bench_test/measurement/state_machine.py`

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> READY: build(session)
    READY --> READY: build(session)
    READY --> RUNNING: start()
    RUNNING --> AGGREGATING: finish()
    RUNNING --> AGGREGATING: stop()
    AGGREGATING --> IDLE: _on_agg_done()
    IDLE --> IDLE: reset() (sessiz, signal yok)
    READY --> IDLE: reset() (sessiz, signal yok)
    RUNNING --> IDLE: reset() (sessiz, timer stop)
    AGGREGATING --> IDLE: reset() (sessiz)
```

## Kısa Notlar

- Geçersiz state çağrılarında geçiş yapılmaz; sadece `log_signal` ile uyarı loglanır.
- `RUNNING` durumunda `QTimer`, `AGGREGATE_INTERVAL_MS` aralığında periyodik aggregate çalıştırır.
- `finish()` ve `stop()` sonrası tek seferlik final aggregate çalışır, ardından `IDLE` durumuna dönülür.
- `state_changed` sinyali yalnızca `_transition()` çağrılarında emit edilir; `reset()` bunu bilerek emit etmez.

