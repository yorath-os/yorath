# Install Yorath — instructions for an AI agent

You are an AI agent with shell access to a server. A person asked you to install Yorath on it. Follow these steps in order. Each step has one command and a sign of success. Do only what is written here.

## Rules

- Run only the commands below, exactly as written. Do not run any other command.
- Do not change the firewall, SSH settings, users or passwords of the server.
- Do not delete files or data, and do not stop or remove containers or services you did not start.
- Do not open the setup wizard link yourself and do not sign in to Yorath: the person does that.
- If any command fails, or its output does not match the sign of success: **stop**. Show the person the command and its last 30 lines of output, suggest running `yorath doctor` if the `yorath` command exists, and ask what to do. Do not try to fix the server yourself.
- Waiting is normal: the install takes about 10 minutes, and on a freshly created server it first waits up to 20 minutes for Ubuntu's automatic updates (the output says "waiting for apt"). Do not interrupt it.

## 1. Check the server

```sh
uname -m && . /etc/os-release && echo "$ID $VERSION_ID" && id -u && nproc && free -g | awk '/Mem/{print $2}'
```

Success: the output is `x86_64`, then `ubuntu 24.04`, then `0` (you are root), then at least `4` (CPUs), then at least `7` (GB of memory).

If the third line is not `0`, use `sudo` in step 3. If the architecture, the system, the CPUs or the memory don't match, stop and tell the person: Yorath needs Ubuntu 24.04 on x86_64 with at least 4 vCPU and 8 GB of memory.

## 2. Check that Yorath is not installed yet

```sh
test -e /opt/yorath && echo "already installed" || echo "clean"
```

Success: `clean`.

If it says `already installed`, stop and tell the person: Yorath is already on this server; to update it they run `yorath update <version>`.

## 3. Install

```sh
curl -fsSL https://get.yorath.io | sh
```

(As a non-root user: `curl -fsSL https://get.yorath.io | sudo sh`.)

Success: the last lines of the output contain `done. Open the setup wizard:` and a link of the form `https://<server address>/?code=<code>`.

## 4. Check the installation

```sh
yorath status
```

Success: the first line starts with `version:` and a version number, and the `services:` line lists `db`, `ollama`, `core` and `web`.

## 5. Report to the person

Tell the person, in one message:

1. Yorath is installed, with the version from step 4.
2. The setup wizard link from step 3. They open it in a browser; the browser will warn about the certificate because Yorath issued it for the server's IP — "Advanced → Proceed" is expected.
3. In the wizard they create the owner, pull the suggested local model and can enable the demo data. The guide: https://yorath.io/docs/get-started/first-hour/

Do not open the link yourself. You are done.
