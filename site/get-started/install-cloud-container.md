---
title: Run on a cloud computer
parent: Get started
nav_order: 3.5
description: Run opncafyr in a container on a Linux cloud computer, next to your data, and open it through SSH or your cloud IDE.
permalink: /get-started/install-cloud-container/
---

# Run on a cloud computer

If your study data lives on a Linux cloud computer where you can run Docker or Podman, run opncafyr there in a container. (Where you can't, a [native Linux install]({{ '/get-started/install-linux/' | relative_url }}) is planned.) Your browser shows the pages; the data and the tools stay on the cloud computer.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

{: .note }
The opncafyr image is private. The person who supports opncafyr at your site gives you access to it, or a file to load with `docker load -i <file>`.

## What you need

- A Linux cloud computer where you can run `docker` or `podman`, and where you are the only user.
- The opncafyr image. It is private: the person who supports opncafyr at your site gives you access, or a file to load (`docker load -i <file>`).
- The `opncafyr-container` script (from the same person, or the `container/` folder of the opncafyr repository).
- Your study folder on the cloud computer's own disk or on a network file share (NFS or SMB). Folders on object storage mounted as a drive (for example s3fs, gcsfuse or blobfuse) are refused, because they can't replace files safely. Copy the study folder to a normal disk first.

## Start it

On the cloud computer, run the line for how you reach it. Each starts opncafyr for one study folder and prints a link.

**Over SSH** (from a terminal on your own computer):

```sh
opncafyr-container start ~/studies/ABC-101
```

It prints an `ssh -L ...` line. Run that line in a terminal on your own computer and leave it open, then open the printed link in your browser.

**In VS Code Server or code-server:** use the URL your IDE shows for port 8765 (its **Ports** panel, or `https://<ide host>/proxy/8765/`):

```sh
opncafyr-container start ~/studies/ABC-101 --access vscode --public-url https://<ide host>/proxy/8765/
```

**In Posit Workbench:** use the URL Workbench shows for port 8765, which looks like `https://<host>/s/<session>/p/<id>/`:

```sh
opncafyr-container start ~/studies/ABC-101 --access workbench --public-url <that URL>
```

**In Jupyter (jupyter-server-proxy):**

```sh
opncafyr-container start ~/studies/ABC-101 --access jupyter --public-url https://<hub host>/user/<you>/proxy/8765/
```

Then open the printed link in the browser where your IDE runs. You are already signed in to the IDE, and that sign-in protects the app.

The link works once, within 2 minutes. To get a new one at any time:

```sh
opncafyr-container url ~/studies/ABC-101
```

For slide previews in decks, add `--full` to `start`. That image includes LibreOffice.

## While it runs

- The study folder is the one you started it with. **Choose folder** opens that folder, and no other.
- If your SSH connection drops or your laptop sleeps, running jobs carry on. Run the `ssh -L` line again, then `opncafyr-container url` for a new link, and you see their progress.
- Running `start` again for the same folder doesn't start a second copy. It prints a new link.
- After 30 minutes with no open page and no running job, opncafyr stops itself, as on a desktop. Use `--idle-timeout off` to keep it running, or `start` again later.

To stop it:

```sh
opncafyr-container stop ~/studies/ABC-101
```

## Keep it private

opncafyr listens only on the cloud computer's own loopback address (`127.0.0.1`). Reach it only through `ssh -L` or your IDE's proxy.

**Never** open its port in a firewall or a cloud security group, and never publish it on a public address. Anyone who could reach the port could try to use your session. The script never does this for you.

## Problems

- **"could not pull" the image:** the image is private. Ask for access, sign in with `docker login ghcr.io` (or `podman login`), or load the image from a file.
- **"object storage ... can't replace files safely":** the study folder is on an object-storage drive. Copy it to the cloud computer's disk or a network file share.
- **The link says "Open opncafyr from its shortcut":** the link was already used or is more than 2 minutes old. Run `opncafyr-container url <study folder>`.
- **Nothing loads over SSH:** check that the `ssh -L` terminal is still open and that you used the port it names.

For anything else, see [Troubleshooting and getting help]({{ '/guides/troubleshooting/' | relative_url }}).
