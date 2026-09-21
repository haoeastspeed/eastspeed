# 代码签名（免费方案 SignPath Foundation）接入指南

目标：让从 GitHub Release 下载的 `DongFangSpeed.exe` 带有受 Windows 信任的数字签名，
消除或显著减少「未知发布者 / Windows 已保护你的电脑（SmartScreen）」警告。

本项目采用 **SignPath Foundation 为合规开源项目提供的免费代码签名**：

- 私钥保存在 SignPath 的硬件安全模块（HSM）中，签名强度接近商业 OV 证书；
- 不收费，但要求项目开源、非商业、许可证合规，并且**产物必须由 GitHub Actions
  从公开源码仓库构建**（不能上传本地 exe 去签）。

> 说明：签名解决的是「发布者可验证、文件未被篡改」。一个全新的签名哈希，
> SmartScreen 信誉仍需一定下载量积累；上架 Edge 商店的扩展本身已由商店背书，
> 两者配合可覆盖绝大多数警告场景。

---

## 一、已经准备好的部分（无需你手写）

- [x] 完整源码公开（**GPL-3.0** 许可证，见 `LICENSE`；与界面框架 PyQt5 的 GPLv3 一致）；
- [x] 从源码可复现构建：`.github/workflows/build.yml`
      在 Windows + Python 3.12 上自动安装依赖、跑测试、
      用 PyInstaller 打包单文件/便携版并执行 `--selftest`；
- [x] 工作流中已写好 **SignPath 签名 + 自动发布 Release** 的完整模板（当前为注释，
      待账号信息就绪后取消注释并填入 slug）；
- [x] `.gitignore` 已排除所有私钥/证书（`*.pfx *.p12 *.key`），密钥不会进仓库。

## 二、需要你在浏览器亲自完成的部分（涉及注册、授权、MFA、审批）

### 1. 注册 SignPath 并申请免费开源证书
1. 打开 <https://signpath.org/> ，选择 Sign Up，使用邮箱
   `haodongfang@hotmail.com` 注册；
2. 创建一个 **Organization（组织）**，名称可用 `eastspeed`；
3. 按 SignPath Foundation 的开源项目申请入口，填写项目信息：
   - 项目名称：East Speed（东方神速）；
   - 代码仓库：`https://github.com/haoeastspeed/eastspeed`；
   - 开源许可证：GPL-3.0（GNU General Public License v3.0，与 PyQt5 GPLv3 兼容）；
   - 用途说明：免费、非商业的 Windows 下载管理器；
4. 按提示为账号开启 **多因素认证（MFA）**（免费证书的强制要求）；
5. 等待 Foundation 审核通过（通常需要一些时间，可能被要求补充项目说明）。

### 2. 安装 SignPath GitHub App
- 在 SignPath 引导下（或 GitHub Marketplace 搜索 “SignPath”），
  把 **SignPath GitHub App** 安装到 `haoeastspeed/eastspeed` 仓库。

### 3. 在 SignPath 后台配置项目
1. **New Project**：Project slug 填 `eastspeed`，关联上面的 GitHub 仓库；
2. **Artifact Configuration**：类型选 Windows 可执行文件（PE/.exe），
   指向工作流产物 `DongFangSpeed.exe`；slug 可用 `release-artifact`；
3. **Signing Policy**：新建 Release 签名策略，slug 用 `release-signing`，
   证书选 Foundation 免费证书，构建来源限定为受信 GitHub Actions；
   免费版通常保留「人工审批签名请求」。
4. 在 **CI/CD Integration** 里创建一个关联本仓库的 CI 链接/服务用户，
   生成 **API Token**，并记下页面上的 **Organization ID**。

### 4. 在 GitHub 仓库配置密钥
进入仓库 `Settings → Secrets and variables → Actions → New repository secret`：

- 名称：`SIGNPATH_API_TOKEN`
- 值：上一步 SignPath 生成的 API Token

### 5. 通知开发者启用工作流签名步骤
把以下三个值提供出来（Organization ID、Project slug、Signing Policy slug、
Artifact Configuration slug），用于替换 `.github/workflows/build.yml`
末尾注释里的 `<...>` 占位并取消注释。Project/Policy/Artifact slug
若按上面命名，则分别是 `eastspeed` / `release-signing` / `release-artifact`。

## 三、发布流程（配置完成后）

推送版本标签即触发「构建 → 签名 → 发布」：

```bash
git tag v1.1.0
git push origin v1.1.0
```

1. GitHub Actions 从公开源码构建出未签名 exe；
2. 自动向 SignPath 提交签名请求；
3. 免费版首次可能需要你在 SignPath 后台点 **Approve（批准）**；
4. 工作流下载已签名 exe，自动创建 GitHub Release 并附带 SHA256 校验值。

## 四、为什么不用其他方案（备选对比）

| 方案 | 结论 |
|---|---|
| SignPath Foundation 免费签名 | **采用**。开源免费、受 Windows 信任、私钥 HSM 托管 |
| Azure Trusted Signing | 约 $9.99/月，且个人开发者目前仅支持美国/加拿大，不适用 |
| 自签证书 | 免费但**不受 Windows 信任**，无法消除 SmartScreen 警告 |
| Sigstore（keyless） | 适合通用制品，**不进入 Windows 受信根**，对 exe 无 SmartScreen 效果 |
| Let's Encrypt | 只签发 TLS/网站证书，**不签发代码签名证书** |
| 商业 OV/EV 证书 | 有效但年费数百至数千元，EV 还常需硬件令牌 |
