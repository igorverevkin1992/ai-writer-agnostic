CREATE TABLE `artifacts` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` text NOT NULL,
	`step` text NOT NULL,
	`version` integer NOT NULL,
	`data` text NOT NULL,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL
);
--> statement-breakpoint
CREATE INDEX `artifacts_step_idx` ON `artifacts` (`project_id`,`step`,`version`);--> statement-breakpoint
CREATE TABLE `characters` (
	`project_id` text NOT NULL,
	`name` text NOT NULL,
	`data` text NOT NULL,
	PRIMARY KEY(`project_id`, `name`)
);
--> statement-breakpoint
CREATE TABLE `check_queue` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` text NOT NULL,
	`target` text NOT NULL,
	`ep` integer NOT NULL,
	`reason` text NOT NULL,
	`done` integer DEFAULT false NOT NULL,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL
);
--> statement-breakpoint
CREATE TABLE `episode_cards` (
	`project_id` text NOT NULL,
	`ep` integer NOT NULL,
	`data` text NOT NULL,
	`version` integer DEFAULT 1 NOT NULL,
	`stale` integer DEFAULT false NOT NULL,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL,
	PRIMARY KEY(`project_id`, `ep`)
);
--> statement-breakpoint
CREATE TABLE `events` (
	`project_id` text NOT NULL,
	`id` text NOT NULL,
	`year` integer NOT NULL,
	`text` text NOT NULL,
	`kind` text DEFAULT 'other' NOT NULL,
	`participants` text NOT NULL,
	`ages` text NOT NULL,
	`after` text NOT NULL,
	PRIMARY KEY(`project_id`, `id`)
);
--> statement-breakpoint
CREATE TABLE `facts` (
	`project_id` text NOT NULL,
	`id` text NOT NULL,
	`text` text NOT NULL,
	`since_ep` integer,
	`source` text DEFAULT 'bible' NOT NULL,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL,
	PRIMARY KEY(`project_id`, `id`)
);
--> statement-breakpoint
CREATE TABLE `findings` (
	`project_id` text NOT NULL,
	`id` text NOT NULL,
	`step` text,
	`controller` text NOT NULL,
	`hole_type` integer,
	`severity` text NOT NULL,
	`episode` integer,
	`quote` text NOT NULL,
	`viewer_question` text NOT NULL,
	`fixes` text NOT NULL,
	`status` text DEFAULT 'open' NOT NULL,
	`resolution_fact_id` text,
	`rule` text,
	`check` text,
	`verdict` text,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL,
	PRIMARY KEY(`project_id`, `id`)
);
--> statement-breakpoint
CREATE TABLE `guns` (
	`project_id` text NOT NULL,
	`id` text NOT NULL,
	`data` text NOT NULL,
	PRIMARY KEY(`project_id`, `id`)
);
--> statement-breakpoint
CREATE TABLE `knowledge` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` text NOT NULL,
	`who` text NOT NULL,
	`fact_id` text NOT NULL,
	`since_ep` integer NOT NULL,
	`how` text
);
--> statement-breakpoint
CREATE INDEX `knowledge_project_idx` ON `knowledge` (`project_id`);--> statement-breakpoint
CREATE TABLE `revisions` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` text NOT NULL,
	`entity` text NOT NULL,
	`entity_id` text NOT NULL,
	`before` text,
	`after` text,
	`author` text NOT NULL,
	`note` text,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL
);
--> statement-breakpoint
CREATE INDEX `revisions_project_idx` ON `revisions` (`project_id`);--> statement-breakpoint
CREATE TABLE `scene_fact_links` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` text NOT NULL,
	`ep` integer NOT NULL,
	`target` text NOT NULL,
	`fact_id` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `links_fact_idx` ON `scene_fact_links` (`project_id`,`fact_id`);--> statement-breakpoint
CREATE TABLE `scripts` (
	`project_id` text NOT NULL,
	`ep` integer NOT NULL,
	`data` text NOT NULL,
	`version` integer DEFAULT 1 NOT NULL,
	`stale` integer DEFAULT false NOT NULL,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL,
	PRIMARY KEY(`project_id`, `ep`)
);
--> statement-breakpoint
CREATE TABLE `steps` (
	`project_id` text NOT NULL,
	`step` text NOT NULL,
	`status` text DEFAULT 'draft' NOT NULL,
	`version` integer DEFAULT 0 NOT NULL,
	`created_at` integer DEFAULT (unixepoch('subsec') * 1000) NOT NULL,
	PRIMARY KEY(`project_id`, `step`)
);
--> statement-breakpoint
CREATE TABLE `world_rules` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` text NOT NULL,
	`data` text NOT NULL
);
