# OCR Sellos y Libros Registrales

Motor OCR ultrarrápido y pipeline de visión artificial especializado en la lectura de digitalizaciones antiguas, texto opaco, sellos oficiales y carátulas de libros del Registro Público.

---

## 🚀 Arquitectura y Tecnologías

* **RapidOCR (`rapidocr-onnxruntime`)**: Inferencia neuronal basada en PP-OCRv4 optimizada en ONNX Runtime. Cero dependencias externas complejas.
* **OpenCV (`cv2`)**: Pipeline de preprocesamiento para **texto opaco**, sellos desteñidos y fondos con viñeteado mediante **CLAHE** (Contrast Limited Adaptive Histogram Equalization) y máscara de desenfoque (*Unsharp Mask*).
* **PyMuPDF (`pymupdf`)**: Renderizado ultrarrápido de páginas PDF directo a memoria RAM (sin tocar disco).

---

## 📂 Reglas de Selección y Negocio

1. **Selección Inteligente de PDFs**:
   * **Carátula**: Se analiza siempre si existe un archivo con `CARATULA` en el nombre.
   * **Primero**: El primer PDF regular.
   * **Enmedio**: El PDF situado a la mitad del conjunto.
   * **Penúltimo**: El penúltimo PDF del conjunto.
   * **Páginas**: Se analizan exclusivamente las primeras 3 páginas de cada documento seleccionado.

2. **Estructura de la Carpeta**:
   * Posición 0: Delegación (ignorada).
   * Posición 1: **Sección** (ej. `01`, `02`, o nombres como `COMERCIO` = 2, `PROPIEDAD` = 1).
   * Posición 2: **Libro** (numérico o alfanumérico).
   * Posición 3: **Tomo** (arábigos, romanos o en palabras).
   * Posición 4: **Volumen**.
   * Posición 5: **Fraccionamiento** (nombre textual de predio/fraccionamiento).
   * Posición 6: **Año** (4 dígitos o escrito en español: *DOS MIL CINCO* = 2005).

3. **Regla Especial `AUXILIAR`**:
   * Si cualquiera de los documentos contiene la palabra clave **`AUXILIAR`**, el libro se clasifica automáticamente como **`AUXILIAR`**.

---

## 🧪 Pruebas Unitarias y de Integración

Ejecutar la suite completa de pruebas:

```bash
# Solo pruebas unitarias rápidas (0.003s)
python run_tests.py --unit

# Suite completa (incluyendo integración con la carpeta real de red)
python run_tests.py
```

---

## 💻 Ejecución Interactiva

Puedes analizar cualquier carpeta ejecutando:

```bash
# Modo interactivo (abre explorador de archivos para elegir la carpeta)
python run_folder_test.py

# O pasando la ruta directa
python run_folder_test.py "\\172.40.5.84\ssdirec\...\01_01_03_02_00_00_2005_REVISAR_SEPARADO"
```
