import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  subsplease_releases: defineTable({
    title: v.string(),
    category: v.string(),
    link: v.string(),
    fileSize: v.optional(v.number()),
    status: v.string(), // "pending", "uploaded_file", "uploaded_text", "failed"
    telegramMessageId: v.optional(v.string()),
    telegramFileUniqueId: v.optional(v.string()),
    addedTime: v.string(),
  })
    .index("by_link", ["link"])
    .index("by_status", ["status"]),

  tsukihime_releases: defineTable({
    title: v.string(),
    category: v.string(),
    link: v.string(),
    fileSize: v.optional(v.number()),
    status: v.string(), // "pending", "uploaded_file", "uploaded_text", "skipped", "failed"
    telegramMessageId: v.optional(v.string()),
    telegramFileUniqueId: v.optional(v.string()),
    addedTime: v.string(),
    seeders: v.optional(v.number()),
  })
    .index("by_link", ["link"])
    .index("by_status", ["status"]),
});
