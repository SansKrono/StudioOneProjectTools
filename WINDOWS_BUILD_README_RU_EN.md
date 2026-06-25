# Studio One Project Tools v2 - Windows build / Сборка под Windows

## RU

В этом архиве подготовлен комплект для сборки Windows EXE.

### Как собрать EXE

1. Распакуйте архив.
2. Убедитесь, что установлен Python 3.11+.
3. Запустите:

```bat
BUILD_WINDOWS_EXE.bat
```

4. Готовая программа появится здесь:

```text
dist\StudioOneProjectTools\StudioOneProjectTools.exe
```

Важно: `es.exe` должен лежать рядом с `StudioOneProjectTools.exe`. В текущей сборке он уже добавлен в комплект.

### Как собрать релизный ZIP

После успешной сборки EXE запустите:

```bat
MAKE_WINDOWS_RELEASE_ZIP.bat
```

Он создаст ZIP с программой, мануалом и промо-файлом.

### Почему не one-file EXE

Для beta-stable выбран onedir-формат. Он надёжнее, потому что `es.exe` лежит рядом с EXE и корректно определяется приложением. One-file можно сделать позже, но для стабильности сейчас лучше onedir.

---

## EN

This archive contains a prepared Windows EXE build kit.

### How to build the EXE

1. Extract the archive.
2. Make sure Python 3.11+ is installed.
3. Run:

```bat
BUILD_WINDOWS_EXE.bat
```

4. The application will be created here:

```text
dist\StudioOneProjectTools\StudioOneProjectTools.exe
```

Important: `es.exe` must stay next to `StudioOneProjectTools.exe`. It is already included in this build kit.

### How to create a release ZIP

After the EXE build is complete, run:

```bat
MAKE_WINDOWS_RELEASE_ZIP.bat
```

It creates a ZIP package with the app, manual and promo file.

### Why onedir instead of one-file

The beta-stable build uses the onedir format for reliability. `es.exe` stays next to the main EXE and is detected automatically. One-file packaging can be added later, but onedir is safer for the current beta.
