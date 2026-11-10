import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { test } from "node:test"
import { tmpdir } from "node:os"
import { join } from "node:path"

import {
  extractTranscript,
  fingerprintMessages,
  normalizeCandidates,
  persistMemoryCandidates,
  scrubSensitiveContent,
} from "../plugins/mify-memory-inject.js"

test("extractTranscript removes private blocks from message text", () => {
  const transcript = extractTranscript([
    {
      info: { id: "m1", role: "user" },
      parts: [
        { type: "text", text: "visible <private>secret</private> content" },
      ],
    },
  ])

  assert.equal(transcript.includes("secret"), false)
  assert.equal(transcript.includes("visible"), true)
})

test("fingerprintMessages is stable for the same message timeline", () => {
  const messages = [
    { info: { id: "m1", role: "user" }, parts: [{ type: "text", text: "hello" }] },
  ]

  assert.equal(fingerprintMessages(messages), fingerprintMessages(messages))
  assert.notEqual(
    fingerprintMessages(messages),
    fingerprintMessages([
      { info: { id: "m2", role: "user" }, parts: [{ type: "text", text: "hello" }] },
    ]),
  )
})

test("normalizeCandidates accepts JSON and removes invalid/private entries", () => {
  const candidates = normalizeCandidates(
    JSON.stringify([
      { content: "Keep this decision", keywords: ["decision"], permanent: true },
      { content: "<private>drop this</private>" },
      { content: "" },
      { content: "Keep this decision", keywords: ["duplicate"] },
    ]),
  )

  assert.deepEqual(candidates, [
    { content: "Keep this decision", keywords: ["decision"], permanent: true },
  ])
})

test("scrubSensitiveContent removes common credential formats", () => {
  const datasetKey = ["dataset", "AbCdEfGh1234"].join("-")
  const shortDatasetKey = ["dataset", "short"].join("-")
  const bearerKey = ["sk", "AbCdEfGh1234"].join("-")
  const rawKey = ["sk", "RawKey1234"].join("-")
  const scrubbed = scrubSensitiveContent(
    `api_key=${datasetKey} ${shortDatasetKey} access_token=oauth-value password=hunter2 Bearer ${bearerKey} ${rawKey}`,
  )

  assert.equal(scrubbed.includes(datasetKey), false)
  assert.equal(scrubbed.includes(shortDatasetKey), false)
  assert.equal(scrubbed.includes("oauth-value"), false)
  assert.equal(scrubbed.includes("hunter2"), false)
  assert.equal(scrubbed.includes(bearerKey), false)
  assert.equal(scrubbed.includes(rawKey), false)
})

test("persistMemoryCandidates returns a non-throwing failure on HTTP errors", async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response("bad", { status: 503 })

  try {
    const result = await persistMemoryCandidates(
      [{ content: "A durable fact", keywords: ["fact"], permanent: false }],
      {
        MIFY_API_KEY: "",
        MIFY_DATASET_ID: "dataset-id",
        MIFY_DOCUMENT_ID: "document-id",
      },
    )

    assert.deepEqual(result, { saved: 0, skipped: 1 })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test("post-compaction hook extracts after compaction and isolates its child session", async () => {
  const configDir = await mkdtemp(join(tmpdir(), "mify-memory-plugin-"))
  const originalConfigDir = process.env.OPENCODE_CONFIG_DIR
  const originalFetch = globalThis.fetch
  process.env.OPENCODE_CONFIG_DIR = configDir
  await writeFile(
    join(configDir, "mify-memory-plugin.jsonc"),
    JSON.stringify({ postCompactionMemory: { enabled: true, minMessages: 4 } }),
  )
  await writeFile(
    join(configDir, "opencode.json"),
    JSON.stringify({
      mcp: {
        memory: {
          environment: {
            MIFY_API_KEY: "",
            MIFY_DATASET_ID: "dataset-id",
            MIFY_DOCUMENT_ID: "document-id",
          },
        },
      },
    }),
  )

  const systemTransforms = []
  const deleted = []
  let hooks
  const client = {
    session: {
      messages: async () => ({
        data: [
          { info: { id: "m1", role: "user" }, parts: [{ type: "text", text: "one" }] },
          { info: { id: "m2", role: "assistant" }, parts: [{ type: "text", text: "two" }] },
          { info: { id: "m3", role: "user" }, parts: [{ type: "text", text: "three" }] },
          { info: { id: "m4", role: "assistant" }, parts: [{ type: "text", text: "four" }] },
        ],
      }),
      create: async () => ({ data: { id: "child-session" } }),
      prompt: async () => {
        const output = { system: [] }
        await hooks["experimental.chat.system.transform"](
          { sessionID: "child-session" },
          output,
        )
        systemTransforms.push(output.system)
        return { data: { parts: [{ type: "text", text: "[{\"content\":\"Keep this\",\"keywords\":[\"fact\"]}]" }] } }
      },
      delete: async ({ path }) => {
        deleted.push(path.id)
      },
    },
  }
  globalThis.fetch = async () => new Response(JSON.stringify({ data: [{}] }), { status: 200 })

  try {
    const plugin = await import(`../plugins/mify-memory-inject.js?test=${Date.now()}`)
    hooks = await plugin.default({ client, directory: configDir })
    await hooks["experimental.compaction.autocontinue"](
      {
        sessionID: "main-session",
        model: { providerID: "provider", modelID: "model" },
      },
      { enabled: false },
    )

    assert.deepEqual(systemTransforms, [[]])
    assert.deepEqual(deleted, ["child-session"])
  } finally {
    globalThis.fetch = originalFetch
    if (originalConfigDir === undefined) delete process.env.OPENCODE_CONFIG_DIR
    else process.env.OPENCODE_CONFIG_DIR = originalConfigDir
    await rm(configDir, { recursive: true, force: true })
  }
})
