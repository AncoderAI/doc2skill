#!/usr/bin/env node
'use strict';

const { runCli } = require('../npm/installer');

try {
  process.exitCode = runCli(process.argv.slice(2));
} catch (error) {
  console.error(`book-to-skill: ${error.message}`);
  process.exitCode = 1;
}
