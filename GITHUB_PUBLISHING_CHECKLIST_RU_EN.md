# GitHub publishing checklist / Чеклист публикации на GitHub

## RU

1. Создайте новый репозиторий на GitHub:
   `StudioOneProjectTools`

2. Распакуйте `StudioOneProjectTools_v2_GitHub_Ready.zip`.

3. Внутри папки выполните:

```bash
git init
git add .
git commit -m "Initial beta stable RC1"
git branch -M main
git remote add origin https://github.com/YOUR_NAME/StudioOneProjectTools.git
git push -u origin main
```

4. Для релиза:
   - откройте GitHub → Releases → Draft a new release;
   - tag: `v2-beta-stable-rc1`;
   - title: `Studio One Project Tools v2 Beta Stable RC1`;
   - вставьте текст из `RELEASE_NOTES.md`;
   - прикрепите Windows release ZIP как asset.

5. Не коммитьте `es.exe` в публичный репозиторий, пока не проверены условия распространения. Для release asset можно приложить bundled build только если условия это позволяют.

## EN

1. Create a new GitHub repository:
   `StudioOneProjectTools`

2. Extract `StudioOneProjectTools_v2_GitHub_Ready.zip`.

3. Run inside the folder:

```bash
git init
git add .
git commit -m "Initial beta stable RC1"
git branch -M main
git remote add origin https://github.com/YOUR_NAME/StudioOneProjectTools.git
git push -u origin main
```

4. For the release:
   - open GitHub → Releases → Draft a new release;
   - tag: `v2-beta-stable-rc1`;
   - title: `Studio One Project Tools v2 Beta Stable RC1`;
   - paste the text from `RELEASE_NOTES.md`;
   - attach the Windows release ZIP as an asset.

5. Do not commit `es.exe` to a public repository unless its redistribution terms are confirmed. Use bundled release assets only if allowed.
