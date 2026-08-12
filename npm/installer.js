'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const packageRoot = path.resolve(__dirname, '..');
const packageMetadata = require('../package.json');
const skillName = 'book-to-skill';
const manifestName = '.book-to-skill-install.json';
const payloadEntries = [
  'SKILL.md',
  'LICENSE.md',
  'references',
  'book_to_skill',
  'scripts/banner.txt',
  'scripts/extract.py',
  'tools',
];

const hostRoots = {
  agents: ['.agents', 'skills'],
  amp: ['.agents', 'skills'],
  claude: ['.claude', 'skills'],
  codex: ['.agents', 'skills'],
  copilot: ['.copilot', 'skills'],
};

function usage() {
  return `Install the book-to-skill Agent Skill.

Usage:
  book-to-skill install [--host <host> | --target <skills-root>] [--force]
  book-to-skill update [--host <host> | --target <skills-root>] [--force]
  book-to-skill doctor [--host <host> | --target <skills-root>] [--json]
  book-to-skill uninstall [--host <host> | --target <skills-root>] [--force]
  book-to-skill version

Hosts:
  codex     ~/.agents/skills (default)
  agents    ~/.agents/skills
  amp       ~/.agents/skills
  claude    ~/.claude/skills
  copilot   ~/.copilot/skills

Examples:
  npm install --global book-to-skill
  book-to-skill install
  book-to-skill install --host claude
  npx --yes book-to-skill@latest install
`;
}

function parseArgs(argv) {
  const options = {
    command: 'help',
    force: false,
    host: null,
    json: false,
    target: null,
  };

  let index = 0;
  if (argv[0] && !argv[0].startsWith('-')) {
    options.command = argv[0];
    index = 1;
  }

  for (; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--force') {
      options.force = true;
    } else if (argument === '--json') {
      options.json = true;
    } else if (argument === '--help' || argument === '-h') {
      options.command = 'help';
    } else if (argument === '--version' || argument === '-v') {
      options.command = 'version';
    } else if (argument === '--host' || argument === '--target') {
      const value = argv[index + 1];
      if (!value || value.startsWith('-')) {
        throw new Error(`${argument} requires a value`);
      }
      options[argument.slice(2)] = value;
      index += 1;
    } else {
      throw new Error(`unknown argument: ${argument}`);
    }
  }

  if (options.host && options.target) {
    throw new Error('--host and --target are mutually exclusive');
  }

  return options;
}

function expandHome(inputPath, homeDir) {
  if (inputPath === '~') {
    return homeDir;
  }
  if (inputPath.startsWith(`~${path.sep}`) || inputPath.startsWith('~/')) {
    return path.join(homeDir, inputPath.slice(2));
  }
  return inputPath;
}

function resolveSkillsRoot(options = {}) {
  const homeDir = options.homeDir || os.homedir();
  if (options.target) {
    const resolved = path.resolve(expandHome(options.target, homeDir));
    if (resolved === path.parse(resolved).root) {
      throw new Error('refusing to use a filesystem root as the skills directory');
    }
    return resolved;
  }

  const host = (options.host || 'codex').toLowerCase();
  const segments = hostRoots[host];
  if (!segments) {
    throw new Error(`unsupported host "${host}"; expected ${Object.keys(hostRoots).join(', ')}`);
  }
  return path.join(homeDir, ...segments);
}

function sha256(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function toPortablePath(filePath) {
  return filePath.split(path.sep).join('/');
}

function collectFiles(root, relativePath, files) {
  const absolutePath = path.join(root, relativePath);
  const stat = fs.lstatSync(absolutePath);
  if (stat.isSymbolicLink()) {
    throw new Error(`symbolic links are not allowed in the npm payload: ${relativePath}`);
  }
  if (stat.isDirectory()) {
    const children = fs.readdirSync(absolutePath).sort();
    for (const child of children) {
      if (child === '__pycache__' || child === '.DS_Store') {
        continue;
      }
      collectFiles(root, path.join(relativePath, child), files);
    }
    return;
  }
  if (!stat.isFile()) {
    throw new Error(`unsupported npm payload entry: ${relativePath}`);
  }
  if (/\.(?:py[co]|swp)$/.test(relativePath) || path.basename(relativePath) === '.DS_Store') {
    return;
  }
  files.push({
    absolutePath,
    mode: stat.mode & 0o777,
    path: toPortablePath(relativePath),
    sha256: sha256(absolutePath),
  });
}

function payloadFiles() {
  const files = [];
  for (const entry of payloadEntries) {
    const absolutePath = path.join(packageRoot, entry);
    if (!fs.existsSync(absolutePath)) {
      throw new Error(`npm package is missing required payload entry: ${entry}`);
    }
    collectFiles(packageRoot, entry, files);
  }
  return files.sort((left, right) => left.path.localeCompare(right.path));
}

function copyFile(source, destination, mode) {
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
  if (process.platform !== 'win32') {
    fs.chmodSync(destination, mode);
  }
}

function createStagedSkill(skillsRoot, options = {}) {
  fs.mkdirSync(skillsRoot, { recursive: true });
  const stagingDir = fs.mkdtempSync(path.join(skillsRoot, `.${skillName}-install-`));
  const files = payloadFiles();
  try {
    for (const file of files) {
      copyFile(file.absolutePath, path.join(stagingDir, file.path), file.mode);
    }
    const manifest = {
      schemaVersion: 1,
      package: packageMetadata.name,
      skill: skillName,
      version: packageMetadata.version,
      host: options.target ? 'custom' : (options.host || 'codex'),
      installedAt: new Date().toISOString(),
      files: files.map(({ path: relativePath, sha256: digest }) => ({
        path: relativePath,
        sha256: digest,
      })),
    };
    fs.writeFileSync(
      path.join(stagingDir, manifestName),
      `${JSON.stringify(manifest, null, 2)}\n`,
      'utf8',
    );
    return { files, manifest, stagingDir };
  } catch (error) {
    fs.rmSync(stagingDir, { recursive: true, force: true });
    throw error;
  }
}

function readManifest(skillDir) {
  const manifestPath = path.join(skillDir, manifestName);
  if (!fs.existsSync(manifestPath)) {
    return null;
  }
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch (error) {
    throw new Error(`invalid install manifest at ${manifestPath}: ${error.message}`);
  }
  if (
    manifest.schemaVersion !== 1
    || manifest.package !== packageMetadata.name
    || manifest.skill !== skillName
    || !Array.isArray(manifest.files)
  ) {
    throw new Error(`unsupported install manifest at ${manifestPath}`);
  }
  for (const file of manifest.files) {
    const relativePath = file?.path;
    const normalized = typeof relativePath === 'string' ? path.posix.normalize(relativePath) : '';
    if (
      !relativePath
      || relativePath === '.'
      || relativePath === manifestName
      || relativePath.startsWith('/')
      || relativePath.includes('\\')
      || normalized !== relativePath
      || normalized.startsWith('../')
      || !/^[a-f0-9]{64}$/.test(file.sha256 || '')
    ) {
      throw new Error(`unsafe managed file entry in ${manifestPath}`);
    }
  }
  return manifest;
}

function assertManagedDirectoryIsNotSymlink(skillDir) {
  if (fs.existsSync(skillDir) && fs.lstatSync(skillDir).isSymbolicLink()) {
    throw new Error(`refusing to manage a symbolic-link skill directory: ${skillDir}`);
  }
}

function hasExpectedSkillName(skillDir) {
  const skillPath = path.join(skillDir, 'SKILL.md');
  if (!fs.existsSync(skillPath)) {
    return false;
  }
  const content = fs.readFileSync(skillPath, 'utf8');
  return /^---\s*[\s\S]*?^name:\s*["']?book-to-skill["']?\s*$/m.test(content);
}

function modifiedManagedFiles(skillDir, manifest) {
  const modified = [];
  for (const file of manifest.files) {
    const filePath = path.join(skillDir, file.path);
    if (!fs.existsSync(filePath)) {
      continue;
    }
    const stat = fs.lstatSync(filePath);
    if (!stat.isFile() || sha256(filePath) !== file.sha256) {
      modified.push(file.path);
    }
  }
  return modified;
}

function collectExistingFiles(root, relativePath = '', files = []) {
  const absolutePath = relativePath ? path.join(root, relativePath) : root;
  for (const child of fs.readdirSync(absolutePath).sort()) {
    const childRelativePath = relativePath ? path.join(relativePath, child) : child;
    const childAbsolutePath = path.join(root, childRelativePath);
    const stat = fs.lstatSync(childAbsolutePath);
    if (stat.isSymbolicLink()) {
      throw new Error(`refusing to update an installation containing a symbolic link: ${childRelativePath}`);
    }
    if (stat.isDirectory()) {
      collectExistingFiles(root, childRelativePath, files);
    } else if (stat.isFile()) {
      files.push({
        absolutePath: childAbsolutePath,
        mode: stat.mode & 0o777,
        path: toPortablePath(childRelativePath),
      });
    }
  }
  return files;
}

function replaceDirectory(stagingDir, skillDir) {
  const backupDir = `${skillDir}.backup-${process.pid}-${Date.now()}`;
  const hadExisting = fs.existsSync(skillDir);
  try {
    if (hadExisting) {
      fs.renameSync(skillDir, backupDir);
    }
    fs.renameSync(stagingDir, skillDir);
    if (hadExisting) {
      fs.rmSync(backupDir, { recursive: true, force: true });
    }
  } catch (error) {
    if (fs.existsSync(skillDir) && !hadExisting) {
      fs.rmSync(skillDir, { recursive: true, force: true });
    }
    if (hadExisting && fs.existsSync(backupDir) && !fs.existsSync(skillDir)) {
      fs.renameSync(backupDir, skillDir);
    }
    if (fs.existsSync(stagingDir)) {
      fs.rmSync(stagingDir, { recursive: true, force: true });
    }
    throw error;
  }
}

function installSkill(options = {}) {
  const skillsRoot = resolveSkillsRoot(options);
  const skillDir = path.join(skillsRoot, skillName);
  assertManagedDirectoryIsNotSymlink(skillDir);
  if (fs.existsSync(skillDir)) {
    if (!options.force) {
      throw new Error(`skill already exists at ${skillDir}; use update, or install --force for a recognized legacy copy`);
    }
    if (!hasExpectedSkillName(skillDir)) {
      throw new Error(`refusing to replace an unrelated directory at ${skillDir}`);
    }
  }

  const staged = createStagedSkill(skillsRoot, options);
  replaceDirectory(staged.stagingDir, skillDir);
  return {
    action: 'installed',
    files: staged.manifest.files.length,
    path: skillDir,
    version: packageMetadata.version,
  };
}

function updateSkill(options = {}) {
  const skillsRoot = resolveSkillsRoot(options);
  const skillDir = path.join(skillsRoot, skillName);
  assertManagedDirectoryIsNotSymlink(skillDir);
  if (!fs.existsSync(skillDir)) {
    throw new Error(`skill is not installed at ${skillDir}; run install first`);
  }

  const manifest = readManifest(skillDir);
  if (!manifest) {
    if (!options.force) {
      throw new Error(`legacy installation has no ${manifestName}; run install --force to migrate it`);
    }
    return installSkill(options);
  }

  const modified = modifiedManagedFiles(skillDir, manifest);
  if (modified.length > 0 && !options.force) {
    throw new Error(`managed files were modified (${modified.join(', ')}); rerun update --force to replace them`);
  }

  const staged = createStagedSkill(skillsRoot, options);
  const oldManaged = new Set(manifest.files.map((file) => file.path));
  const newManaged = new Set(staged.manifest.files.map((file) => file.path));
  const existingFiles = collectExistingFiles(skillDir);

  try {
    for (const file of existingFiles) {
      if (file.path === manifestName || oldManaged.has(file.path)) {
        continue;
      }
      if (newManaged.has(file.path)) {
        if (!options.force) {
          throw new Error(`unmanaged file conflicts with the new package payload: ${file.path}`);
        }
        continue;
      }
      copyFile(file.absolutePath, path.join(staged.stagingDir, file.path), file.mode);
    }
    replaceDirectory(staged.stagingDir, skillDir);
  } catch (error) {
    if (fs.existsSync(staged.stagingDir)) {
      fs.rmSync(staged.stagingDir, { recursive: true, force: true });
    }
    throw error;
  }

  return {
    action: 'updated',
    files: staged.manifest.files.length,
    path: skillDir,
    version: packageMetadata.version,
  };
}

function removeEmptyParents(skillDir, relativePaths) {
  const directories = new Set();
  for (const relativePath of relativePaths) {
    let directory = path.dirname(relativePath);
    while (directory !== '.' && directory !== path.sep) {
      directories.add(directory);
      directory = path.dirname(directory);
    }
  }
  const ordered = [...directories].sort((left, right) => right.length - left.length);
  for (const directory of ordered) {
    const absolutePath = path.join(skillDir, directory);
    if (fs.existsSync(absolutePath) && fs.readdirSync(absolutePath).length === 0) {
      fs.rmdirSync(absolutePath);
    }
  }
}

function uninstallSkill(options = {}) {
  const skillsRoot = resolveSkillsRoot(options);
  const skillDir = path.join(skillsRoot, skillName);
  assertManagedDirectoryIsNotSymlink(skillDir);
  if (!fs.existsSync(skillDir)) {
    throw new Error(`skill is not installed at ${skillDir}`);
  }
  const manifest = readManifest(skillDir);
  if (!manifest) {
    throw new Error(`refusing to uninstall a directory without ${manifestName}`);
  }

  const modified = modifiedManagedFiles(skillDir, manifest);
  if (modified.length > 0 && !options.force) {
    throw new Error(`managed files were modified (${modified.join(', ')}); rerun uninstall --force to remove them`);
  }

  for (const file of manifest.files) {
    const filePath = path.join(skillDir, file.path);
    if (fs.existsSync(filePath)) {
      fs.rmSync(filePath, { force: true });
    }
  }
  fs.rmSync(path.join(skillDir, manifestName), { force: true });
  removeEmptyParents(skillDir, manifest.files.map((file) => file.path));
  if (fs.readdirSync(skillDir).length === 0) {
    fs.rmdirSync(skillDir);
  }

  return {
    action: 'uninstalled',
    path: skillDir,
    preservedUserFiles: fs.existsSync(skillDir),
    version: manifest.version,
  };
}

function detectPython() {
  for (const executable of ['python3', 'python']) {
    const result = spawnSync(executable, ['--version'], { encoding: 'utf8' });
    if (result.status === 0) {
      return `${executable}: ${(result.stdout || result.stderr).trim()}`;
    }
  }
  return null;
}

function doctorSkill(options = {}) {
  const skillsRoot = resolveSkillsRoot(options);
  const skillDir = path.join(skillsRoot, skillName);
  const issues = [];
  const warnings = [];

  if (!fs.existsSync(skillDir)) {
    issues.push(`skill is not installed at ${skillDir}`);
    return { issues, path: skillDir, python: detectPython(), version: null, warnings };
  }

  if (fs.lstatSync(skillDir).isSymbolicLink()) {
    issues.push(`refusing to inspect a symbolic-link skill directory: ${skillDir}`);
    return { issues, path: skillDir, python: detectPython(), version: null, warnings };
  }

  let manifest = null;
  try {
    manifest = readManifest(skillDir);
  } catch (error) {
    issues.push(error.message);
  }
  if (!manifest) {
    issues.push(`missing ${manifestName}`);
  } else {
    for (const file of manifest.files) {
      const filePath = path.join(skillDir, file.path);
      if (!fs.existsSync(filePath)) {
        issues.push(`missing managed file: ${file.path}`);
      } else if (!fs.lstatSync(filePath).isFile()) {
        issues.push(`managed path is not a regular file: ${file.path}`);
      } else if (sha256(filePath) !== file.sha256) {
        issues.push(`managed file was modified: ${file.path}`);
      }
    }
  }

  const python = detectPython();
  if (!python) {
    warnings.push('Python 3.9+ is required to run the extraction engine');
  }

  return {
    issues,
    path: skillDir,
    python,
    version: manifest ? manifest.version : null,
    warnings,
  };
}

function printResult(result, json) {
  if (json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (result.action) {
    console.log(`book-to-skill ${result.action}: ${result.path}`);
    console.log(`version: ${result.version}`);
    if (typeof result.files === 'number') {
      console.log(`managed files: ${result.files}`);
    }
    if (result.preservedUserFiles) {
      console.log('user-managed files were preserved in the skill directory');
    }
    return;
  }
  console.log(`book-to-skill doctor: ${result.path}`);
  console.log(`version: ${result.version || 'unknown'}`);
  console.log(`python: ${result.python || 'not found'}`);
  for (const warning of result.warnings) {
    console.log(`warning: ${warning}`);
  }
  for (const issue of result.issues) {
    console.error(`issue: ${issue}`);
  }
  if (result.issues.length === 0) {
    console.log('status: ok');
  }
}

function runCli(argv) {
  const options = parseArgs(argv);
  if (options.command === 'help') {
    console.log(usage());
    return 0;
  }
  if (options.command === 'version') {
    console.log(packageMetadata.version);
    return 0;
  }

  let result;
  if (options.command === 'install') {
    result = installSkill(options);
  } else if (options.command === 'update') {
    result = updateSkill(options);
  } else if (options.command === 'uninstall') {
    result = uninstallSkill(options);
  } else if (options.command === 'doctor') {
    result = doctorSkill(options);
  } else {
    throw new Error(`unknown command: ${options.command}`);
  }

  printResult(result, options.json);
  return result.issues && result.issues.length > 0 ? 1 : 0;
}

module.exports = {
  doctorSkill,
  installSkill,
  manifestName,
  parseArgs,
  payloadFiles,
  resolveSkillsRoot,
  runCli,
  uninstallSkill,
  updateSkill,
  usage,
};
