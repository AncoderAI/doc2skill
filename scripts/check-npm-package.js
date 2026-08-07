#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const packagePath = path.join(root, 'package.json');
const packageLockPath = path.join(root, 'package-lock.json');
const pyprojectPath = path.join(root, 'pyproject.toml');
const skillPath = path.join(root, 'SKILL.md');
const packageMetadata = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

const errors = [];
function requireCondition(condition, message) {
  if (!condition) {
    errors.push(message);
  }
}

requireCondition(packageMetadata.name === 'book-to-skill', 'package name must be book-to-skill');
requireCondition(packageMetadata.license === 'MIT', 'package license must be MIT');
requireCondition(packageMetadata.bin?.['book-to-skill'] === 'bin/book-to-skill-skill.js', 'primary npm bin mapping is invalid');
requireCondition(packageMetadata.bin?.['book-to-skill-skill'] === 'bin/book-to-skill-skill.js', 'legacy npm bin mapping is invalid');
requireCondition(packageMetadata.publishConfig?.access === 'public', 'publishConfig.access must be public');
requireCondition(packageMetadata.repository?.url === 'git+https://github.com/AncoderAI/doc2skill.git', 'repository URL must match the publishing repository');

const requiredDiskFiles = [
  'SKILL.md',
  'README.md',
  'LICENSE.md',
  'book_to_skill/cli.py',
  'book_to_skill/parsers/pdf.py',
  'scripts/banner.txt',
  'scripts/extract.py',
  'scripts/check-npm-package.js',
  'tools/scan_generated_skill.py',
  'bin/book-to-skill-skill.js',
  'npm/installer.js',
];
const publishedFiles = new Set(packageMetadata.files || []);
for (const relativePath of requiredDiskFiles) {
  requireCondition(fs.existsSync(path.join(root, relativePath)), `required file is missing: ${relativePath}`);
}
const requiredPublishedEntries = [
  'SKILL.md',
  'README.md',
  'LICENSE.md',
  'book_to_skill/*.py',
  'book_to_skill/parsers/*.py',
  'scripts/banner.txt',
  'scripts/extract.py',
  'scripts/check-npm-package.js',
  'tools/*.py',
  'bin/book-to-skill-skill.js',
  'npm/installer.js',
];
for (const entry of requiredPublishedEntries) {
  requireCondition(publishedFiles.has(entry), `package.json files is missing: ${entry}`);
}

const pyproject = fs.readFileSync(pyprojectPath, 'utf8');
const pythonVersion = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
requireCondition(Boolean(pythonVersion), 'could not read version from pyproject.toml');
requireCondition(pythonVersion === packageMetadata.version, `version mismatch: package.json=${packageMetadata.version}, pyproject.toml=${pythonVersion}`);

const skill = fs.readFileSync(skillPath, 'utf8');
requireCondition(/^---\s*[\s\S]*?^name:\s*book-to-skill\s*$/m.test(skill), 'SKILL.md name must be book-to-skill');
requireCondition(/^---\s*[\s\S]*?^description:\s*.+$/m.test(skill), 'SKILL.md must have a description');

if (fs.existsSync(packageLockPath)) {
  const packageLock = JSON.parse(fs.readFileSync(packageLockPath, 'utf8'));
  requireCondition(packageLock.name === packageMetadata.name, 'package-lock.json name does not match package.json');
  requireCondition(packageLock.version === packageMetadata.version, 'package-lock.json version does not match package.json');
  requireCondition(packageLock.packages?.['']?.version === packageMetadata.version, 'package-lock root version does not match package.json');
}

if (process.platform !== 'win32') {
  const binMode = fs.statSync(path.join(root, 'bin/book-to-skill-skill.js')).mode & 0o111;
  requireCondition(binMode !== 0, 'bin/book-to-skill-skill.js must be executable');
}

if (errors.length > 0) {
  console.error('book-to-skill npm package validation failed:');
  for (const error of errors) {
    console.error(`  - ${error}`);
  }
  process.exit(1);
}

console.log(`book-to-skill npm package ${packageMetadata.version}: validation passed`);
