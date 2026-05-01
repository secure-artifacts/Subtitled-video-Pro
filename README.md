# Subtitle Video Pro

这是一个视频字幕处理的高级桌面客户端工具。支持 Windows 和 macOS 双端运行。

---

## 如何发布新版本

本项目使用 GitHub Actions 自动构建和发布。每次发布新版本只需要创建一个 Git Tag 并推送即可。

### 发布步骤

#### 1. 确保代码已提交并推送

在发布之前，确保你的所有代码改动已经提交并推送到 GitHub：
```bash
# 查看当前状态
git status

# 添加所有改动
git add .

# 提交改动（把"你的改动说明"替换成实际的描述）
git commit -m "你的改动说明"

# 推送到 GitHub
git push origin main

2. 创建版本 TagGit Tag 是一个版本标记，用于标识发布的版本号。版本号格式为 v主版本.次版本.修订版本，例如 v1.0.0、v1.1.0、v2.0.0。Bash# 创建一个新的版本 tag（将 v1.0.1 替换为你想要的版本号）
git tag -a v1.0.1 -m "Release version 1.0.1"
3. 推送 Tag 触发自动构建Bash# 推送 tag 到 GitHub（这会自动触发 CI 构建）
git push origin v1.0.1
推送后，GitHub Actions 会自动执行以下操作：构建项目（打包生成 Windows 的 .exe 和 macOS 的 .dmg）生成安全签名（Attestation）创建 Release 并上传构建产物4. 查看构建结果构建进度：访问项目的 Actions 页面查看发布结果：访问项目的 Releases 页面查看已发布的文件版本号说明版本号格式什么时候用示例vX.0.0重大更新、不兼容改动v2.0.0vX.Y.0新增功能v1.1.0vX.Y.Z修复 bugv1.0.1如果构建失败怎么办访问项目的 Actions 页面查看错误日志修复代码问题删除失败的 tag 并重新创建：Bash# 删除本地 tag
git tag -d v1.0.1

# 删除远程 tag
git push origin :refs/tags/v1.0.1

# 修复问题后，重新创建并推送
git tag -a v1.0.1 -m "Release version 1.0.1"
git push origin v1.0.1

把这个 README 更新推送到 GitHub 后，**Step 4 就彻底圆满完成了**[cite: 1]！

接下来，你就可以打个 `v1.0.0` 的 Tag 推送上去触发打包了[cite: 1]。等 Actions 跑
