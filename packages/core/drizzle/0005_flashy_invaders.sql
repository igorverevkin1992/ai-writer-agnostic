CREATE TABLE `llm_cache` (
	`key` text PRIMARY KEY NOT NULL,
	`role` text NOT NULL,
	`model` text NOT NULL,
	`task` text,
	`text` text NOT NULL,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL
);
