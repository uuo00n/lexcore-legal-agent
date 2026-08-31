from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


def test_stream_events_do_not_throw_when_stream_anchor_is_detached(tmp_path):
    script = tmp_path / "check_static_app.js"
    app_path = Path(__file__).resolve().parents[1] / "static" / "app.js"
    script.write_text(
        textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");

            class FakeClassList {{
              add() {{}}
              remove() {{}}
              toggle() {{}}
            }}

            class FakeElement {{
              constructor(tag = "div") {{
                this.tag = tag;
                this.children = [];
                this.parentNode = null;
                this.dataset = {{}};
                this.style = {{}};
                this.classList = new FakeClassList();
                this.hidden = false;
                this.value = "";
                this._innerHTML = "";
                this.textContent = "";
                this.scrollTop = 0;
                this.scrollHeight = 0;
              }}
              appendChild(child) {{
                child.parentNode = this;
                this.children.push(child);
                return child;
              }}
              insertBefore(child, before) {{
                const idx = this.children.indexOf(before);
                if (idx === -1) {{
                  throw new Error("Failed to execute 'insertBefore' on 'Node': The node before which the new node is to be inserted is not a child of this node.");
                }}
                child.parentNode = this;
                this.children.splice(idx, 0, child);
                return child;
              }}
              addEventListener() {{}}
              querySelector(selector) {{
                if (selector === "details") return this.details || new FakeElement("details");
                return new FakeElement("span");
              }}
              closest() {{ return null; }}
              set innerHTML(value) {{
                this._innerHTML = value;
                if (String(value).includes("<details>")) {{
                  this.details = new FakeElement("details");
                  this.appendChild(this.details);
                }}
              }}
              get innerHTML() {{ return this._innerHTML; }}
            }}

            const elements = new Map();
            const missingSelectors = new Set(["#slash-menu"]);
            const messages = new FakeElement("div");
            elements.set("#messages", messages);
            for (const selector of [
              "#doc-chip .doc-remove", "#file-input", "#send", "#input",
              "#new-chat", "#compact-context", "#context-percent",
              "#context-bar", "#context-meta", "#thread-list", "#provider-tag",
              "#doc-chip"
            ]) {{
              elements.set(selector, new FakeElement("div"));
            }}
            elements.get("#context-bar").appendChild(new FakeElement("span"));

            const document = {{
              querySelector(selector) {{
                if (missingSelectors.has(selector)) return null;
                if (!elements.has(selector)) elements.set(selector, new FakeElement("div"));
                return elements.get(selector);
              }},
              createElement(tag) {{
                return new FakeElement(tag);
              }},
            }};

            const context = {{
              document,
              localStorage: {{ getItem: () => null, setItem: () => {{}} }},
              crypto: {{ randomUUID: () => "thread-test" }},
              fetch: async () => ({{ ok: false, json: async () => ({{}}), text: async () => "" }}),
              FormData: class {{}},
              TextDecoder,
              console,
              setTimeout,
              clearTimeout,
            }};
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(app_path)!r}, "utf8"), context);

            const detachedAiMsg = new FakeElement("div");
            context.handleEvent({{ event: "thought", data: JSON.stringify({{ content: "正在检索" }}) }}, detachedAiMsg);
            context.handleEvent({{ event: "tool_start", data: "{{}}" }}, detachedAiMsg);
            context.handleEvent({{
              event: "tool_end",
              data: JSON.stringify({{ name: "retrieve_local_law_tool", output: {{ results: [] }} }}),
            }}, detachedAiMsg);
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=app_path.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_second_thread_can_send_while_first_thread_is_streaming(tmp_path):
    script = tmp_path / "check_multi_thread_send.js"
    app_path = Path(__file__).resolve().parents[1] / "static" / "app.js"
    script.write_text(
        textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");

            class FakeClassList {{
              add() {{}}
              remove() {{}}
              toggle() {{}}
            }}

            class FakeElement {{
              constructor(tag = "div") {{
                this.tag = tag;
                this.children = [];
                this.parentNode = null;
                this.dataset = {{}};
                this.style = {{}};
                this.classList = new FakeClassList();
                this.hidden = false;
                this.disabled = false;
                this.value = "";
                this._innerHTML = "";
                this.textContent = "";
                this.scrollTop = 0;
                this.scrollHeight = 0;
              }}
              appendChild(child) {{
                child.parentNode = this;
                this.children.push(child);
                return child;
              }}
              insertBefore(child, before) {{
                const idx = this.children.indexOf(before);
                if (idx === -1) throw new Error("insertBefore target missing");
                child.parentNode = this;
                this.children.splice(idx, 0, child);
                return child;
              }}
              addEventListener() {{}}
              querySelector(selector) {{
                if (selector === ".doc-name" || selector === ".doc-meta") return new FakeElement("span");
                if (selector === "details") return this.details || new FakeElement("details");
                return new FakeElement("span");
              }}
              closest() {{ return null; }}
              set innerHTML(value) {{
                this._innerHTML = value;
                if (String(value).includes("<details>")) {{
                  this.details = new FakeElement("details");
                  this.appendChild(this.details);
                }}
              }}
              get innerHTML() {{ return this._innerHTML; }}
            }}

            const elements = new Map();
            const messages = new FakeElement("div");
            elements.set("#messages", messages);
            for (const selector of [
              "#doc-chip .doc-remove", "#file-input", "#send", "#input",
              "#slash-menu", "#new-chat", "#compact-context", "#context-percent",
              "#context-bar", "#context-meta", "#thread-list", "#provider-tag",
              "#doc-chip"
            ]) {{
              elements.set(selector, new FakeElement("div"));
            }}
            elements.get("#context-bar").appendChild(new FakeElement("span"));

            const document = {{
              querySelector(selector) {{
                if (!elements.has(selector)) elements.set(selector, new FakeElement("div"));
                return elements.get(selector);
              }},
              createElement(tag) {{
                return new FakeElement(tag);
              }},
            }};

            const chatRequests = [];
            function pendingChatResponse(body) {{
              let release;
              const pending = new Promise((resolve) => {{ release = resolve; }});
              return {{
                request: JSON.parse(body),
                release,
                response: {{
                  ok: true,
                  body: {{
                    getReader() {{
                      return {{
                        read() {{ return pending; }},
                      }};
                    }},
                  }},
                }},
              }};
            }}

            const context = {{
              document,
              localStorage: {{ getItem: () => null, setItem: () => {{}} }},
              crypto: {{ randomUUID: () => "thread-initial" }},
              fetch: async (url, options = {{}}) => {{
                if (url === "/api/chat") {{
                  const item = pendingChatResponse(options.body);
                  chatRequests.push(item);
                  return item.response;
                }}
                return {{ ok: false, json: async () => ({{}}), text: async () => "" }};
              }},
              FormData: class {{}},
              TextDecoder,
              console,
              setTimeout,
              clearTimeout,
            }};
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(app_path)!r}, "utf8"), context);

            (async () => {{
              context.setThread("thread-a");
              const first = context.sendMessage("A question");
              await new Promise((resolve) => setTimeout(resolve, 0));
              if (chatRequests.length !== 1) throw new Error(`expected first chat request, got ${{chatRequests.length}}`);

              context.setThread("thread-b");
              const second = context.sendMessage("B question");
              await new Promise((resolve) => setTimeout(resolve, 0));
              if (chatRequests.length !== 2) throw new Error(`expected second thread to send while first streams, got ${{chatRequests.length}}`);
              if (chatRequests[0].request.thread_id !== "thread-a") throw new Error("first request thread mismatch");
              if (chatRequests[1].request.thread_id !== "thread-b") throw new Error("second request thread mismatch");

              chatRequests.forEach((item) => item.release({{ done: true }}));
              await first;
              await second;
            }})().catch((err) => {{
              console.error(err.stack || err.message);
              process.exitCode = 1;
            }});
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=app_path.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_background_thread_stream_does_not_render_into_current_thread_and_restores_on_return(tmp_path):
    script = tmp_path / "check_background_stream.js"
    app_path = Path(__file__).resolve().parents[1] / "static" / "app.js"
    script.write_text(
        textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");

            class FakeClassList {{
              add() {{}}
              remove() {{}}
              toggle() {{}}
            }}

            class FakeElement {{
              constructor(tag = "div") {{
                this.tag = tag;
                this.children = [];
                this.parentNode = null;
                this.dataset = {{}};
                this.style = {{}};
                this.classList = new FakeClassList();
                this.hidden = false;
                this.disabled = false;
                this.value = "";
                this._innerHTML = "";
                this.textContent = "";
                this.scrollTop = 0;
                this.scrollHeight = 0;
              }}
              appendChild(child) {{
                child.parentNode = this;
                this.children.push(child);
                return child;
              }}
              insertBefore(child, before) {{
                const idx = this.children.indexOf(before);
                if (idx === -1) throw new Error("insertBefore target missing");
                child.parentNode = this;
                this.children.splice(idx, 0, child);
                return child;
              }}
              addEventListener() {{}}
              querySelector(selector) {{
                if (selector === ".doc-name" || selector === ".doc-meta") return new FakeElement("span");
                if (selector === "details") return this.details || new FakeElement("details");
                return new FakeElement("span");
              }}
              closest() {{ return null; }}
              set innerHTML(value) {{
                this._innerHTML = value;
                if (String(value).includes("<details>")) {{
                  this.details = new FakeElement("details");
                  this.appendChild(this.details);
                }}
              }}
              get innerHTML() {{ return this._innerHTML; }}
            }}

            function collectText(el) {{
              return [el.textContent || "", el.innerHTML || "", ...el.children.map(collectText)].join(" ");
            }}

            const elements = new Map();
            const messages = new FakeElement("div");
            elements.set("#messages", messages);
            for (const selector of [
              "#doc-chip .doc-remove", "#file-input", "#send", "#input",
              "#slash-menu", "#new-chat", "#compact-context", "#context-percent",
              "#context-bar", "#context-meta", "#thread-list", "#provider-tag",
              "#doc-chip"
            ]) {{
              elements.set(selector, new FakeElement("div"));
            }}
            elements.get("#context-bar").appendChild(new FakeElement("span"));

            const document = {{
              querySelector(selector) {{
                if (!elements.has(selector)) elements.set(selector, new FakeElement("div"));
                return elements.get(selector);
              }},
              createElement(tag) {{
                return new FakeElement(tag);
              }},
            }};

            const chatRequests = [];
            function streamingChatResponse(body) {{
              const reads = [];
              return {{
                request: JSON.parse(body),
                reads,
                response: {{
                  ok: true,
                  body: {{
                    getReader() {{
                      return {{
                        read() {{
                          return new Promise((resolve) => reads.push(resolve));
                        }},
                      }};
                    }},
                  }},
                }},
              }};
            }}

            const context = {{
              document,
              localStorage: {{ getItem: () => null, setItem: () => {{}} }},
              crypto: {{ randomUUID: () => "thread-initial" }},
              fetch: async (url, options = {{}}) => {{
                if (url === "/api/chat") {{
                  const item = streamingChatResponse(options.body);
                  chatRequests.push(item);
                  return item.response;
                }}
                if (String(url).includes("/history")) {{
                  return {{ ok: true, json: async () => ({{ messages: [] }}) }};
                }}
                return {{ ok: false, json: async () => ({{}}), text: async () => "" }};
              }},
              FormData: class {{}},
              TextDecoder,
              Buffer,
              console,
              setTimeout,
              clearTimeout,
            }};
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(app_path)!r}, "utf8"), context);

            (async () => {{
              context.setThread("thread-a");
              const first = context.sendMessage("A question");
              await new Promise((resolve) => setTimeout(resolve, 0));
              if (chatRequests.length !== 1) throw new Error("first request missing");

              await context.switchThread("thread-b");
              chatRequests[0].reads.shift()({{
                done: false,
                value: Buffer.from("event: token\\ndata: A-token\\n\\n"),
              }});
              await new Promise((resolve) => setTimeout(resolve, 0));
              if (collectText(messages).includes("A-token")) {{
                throw new Error("background token rendered into current thread");
              }}

              await context.switchThread("thread-a");
              await new Promise((resolve) => setTimeout(resolve, 0));
              if (!collectText(messages).includes("A-token")) {{
                throw new Error("background token was not restored when returning to original thread");
              }}

              chatRequests[0].reads.shift()({{ done: true }});
              await first;
            }})().catch((err) => {{
              console.error(err.stack || err.message);
              process.exitCode = 1;
            }});
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=app_path.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
