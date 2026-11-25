## Modelo de ambiente (datos sintéticos)

- Muestras de prueba: 374
- Exactitud: 97.6%
- F1 macro: 0.974

Matriz de confusión (filas: clase real; columnas: predicción).

| Real \ Predicción | `confort` | `riesgo_leve` | `riesgo` |
|---|---:|---:|---:|
| `confort` | 51 | 0 | 0 |
| `riesgo_leve` | 3 | 131 | 3 |
| `riesgo` | 0 | 3 | 183 |

| Clase | Precisión | Sensibilidad | F1 | Muestras |
|---|---:|---:|---:|---:|
| `confort` | 0.944 | 1.000 | 0.971 | 51 |
| `riesgo_leve` | 0.978 | 0.956 | 0.967 | 137 |
| `riesgo` | 0.984 | 0.984 | 0.984 | 186 |

> Datos sintéticos etiquetados con los rangos de confort: prueban el flujo, no son el resultado del modelo. Hay que reentrenar con lecturas reales.
