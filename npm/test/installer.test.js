'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const {
  doctorSkill,
  installSkill,
  manifestName,
  parseArgs,
  resolveSkillsRoot,
  uninstallSkill,
  updateSkill,
} = require('../installer');

const packageRoot = path.resolve(__dirname, '..', '..');
const packageMetadata = require('../../package.json');

function withTempDirectory(callback) {
  const tempDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'book-to-skill-npm-test-'));
  try {
    callback(tempDirectory);
  } finally {
    fs.rmSync(tempDirectory, { recursive: true, force: true });
  }
}

test('package exposes the primary and legacy npm commands', () => {
  assert.equal(packageMetadata.bin['book-to-skill'], 'bin/book-to-skill-skill.js');
  assert.equal(packageMetadata.bin['book-to-skill-skill'], 'bin/book-to-skill-skill.js');
});

test('host paths use the documented skills roots', () => {
  const homeDir = path.join(path.parse(process.cwd()).root, 'test-home');
  assert.equal(resolveSkillsRoot({ homeDir, host: 'codex' }), path.join(homeDir, '.agents', 'skills'));
  assert.equal(resolveSkillsRoot({ homeDir, host: 'agents' }), path.join(homeDir, '.agents', 'skills'));
  assert.equal(resolveSkillsRoot({ homeDir, host: 'amp' }), path.join(homeDir, '.agents', 'skills'));
  assert.equal(resolveSkillsRoot({ homeDir, host: 'claude' }), path.join(homeDir, '.claude', 'skills'));
  assert.equal(resolveSkillsRoot({ homeDir, host: 'copilot' }), path.join(homeDir, '.copilot', 'skills'));
});

test('host and target cannot be combined', () => {
  assert.throws(
    () => parseArgs(['install', '--host', 'claude', '--target', '/tmp/skills']),
    /mutually exclusive/,
  );
});

test('install, doctor, update, and uninstall preserve user-managed files', () => {
  withTempDirectory((tempDirectory) => {
    const skillsRoot = path.join(tempDirectory, 'skills');
    const installResult = installSkill({ host: 'codex', target: skillsRoot });
    const skillDir = path.join(skillsRoot, 'book-to-skill');

    assert.equal(installResult.path, skillDir);
    assert.equal(fs.existsSync(path.join(skillDir, 'SKILL.md')), true);
    assert.equal(fs.existsSync(path.join(skillDir, 'scripts', 'extract.py')), true);
    assert.equal(fs.existsSync(path.join(skillDir, manifestName)), true);
    const manifest = JSON.parse(fs.readFileSync(path.join(skillDir, manifestName), 'utf8'));
    assert.equal(manifest.files.some((file) => file.path.includes('__pycache__')), false);
    assert.equal(manifest.files.some((file) => file.path.endsWith('.pyc')), false);

    const doctor = doctorSkill({ target: skillsRoot });
    assert.deepEqual(doctor.issues, []);

    const userFile = path.join(skillDir, 'local-notes.md');
    fs.writeFileSync(userFile, 'keep me\n', 'utf8');
    const updateResult = updateSkill({ host: 'codex', target: skillsRoot });
    assert.equal(updateResult.action, 'updated');
    assert.equal(fs.readFileSync(userFile, 'utf8'), 'keep me\n');

    const uninstallResult = uninstallSkill({ target: skillsRoot });
    assert.equal(uninstallResult.preservedUserFiles, true);
    assert.equal(fs.existsSync(userFile), true);
    assert.equal(fs.existsSync(path.join(skillDir, 'SKILL.md')), false);
  });
});

test('update refuses modified managed files unless force is explicit', () => {
  withTempDirectory((tempDirectory) => {
    const skillsRoot = path.join(tempDirectory, 'skills');
    installSkill({ target: skillsRoot });
    const skillPath = path.join(skillsRoot, 'book-to-skill', 'SKILL.md');
    fs.appendFileSync(skillPath, '\nlocal edit\n', 'utf8');

    assert.throws(
      () => updateSkill({ target: skillsRoot }),
      /managed files were modified/,
    );
    updateSkill({ force: true, target: skillsRoot });
    assert.equal(fs.readFileSync(skillPath, 'utf8').includes('local edit'), false);
  });
});

test('CLI produces machine-readable installation output', () => {
  withTempDirectory((tempDirectory) => {
    const skillsRoot = path.join(tempDirectory, 'skills');
    const cliPath = path.join(packageRoot, 'bin', 'book-to-skill-skill.js');
    const result = spawnSync(
      process.execPath,
      [cliPath, 'install', '--target', skillsRoot, '--json'],
      { encoding: 'utf8' },
    );

    assert.equal(result.status, 0, result.stderr);
    const output = JSON.parse(result.stdout);
    assert.equal(output.action, 'installed');
    assert.equal(output.path, path.join(skillsRoot, 'book-to-skill'));
  });
});

test('tampered manifests cannot address files outside the skill directory', () => {
  withTempDirectory((tempDirectory) => {
    const skillsRoot = path.join(tempDirectory, 'skills');
    const outsideFile = path.join(skillsRoot, 'outside.txt');
    installSkill({ target: skillsRoot });
    fs.writeFileSync(outsideFile, 'do not remove\n', 'utf8');

    const manifestPath = path.join(skillsRoot, 'book-to-skill', manifestName);
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    manifest.files = [{ path: '../outside.txt', sha256: '0'.repeat(64) }];
    fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

    assert.throws(() => uninstallSkill({ force: true, target: skillsRoot }), /unsafe managed file entry/);
    assert.equal(fs.readFileSync(outsideFile, 'utf8'), 'do not remove\n');
  });
});

test('pdf2md passes its arguments through to the bundled Python CLI', () => {
  const result = spawnSync(
    process.execPath,
    [path.join(packageRoot, 'bin', 'book-to-skill-skill.js'), 'pdf2md', 'doctor', '--json'],
    { cwd: os.tmpdir(), encoding: 'utf8' },
  );

  if (result.status !== 0 && /no Python 3\.9\+ interpreter found/.test(result.stderr)) {
    return; // No interpreter on this machine; the passthrough itself is untestable.
  }

  // doctor exits non-zero when extras are missing, which is the normal state on
  // a bare CI runner. What this asserts is that the passthrough delivered the
  // arguments and returned a real report rather than a traceback.
  assert.equal(result.stderr, '', 'doctor should not fail with a Python traceback');
  const report = JSON.parse(result.stdout.slice(result.stdout.indexOf('{')));
  assert.equal(typeof report.ok, 'boolean');
  assert.ok(report.binaries, 'doctor report should carry a binaries section');
});

test('pdf2md reports a usable error when no interpreter can be found', () => {
  const result = spawnSync(
    process.execPath,
    [path.join(packageRoot, 'bin', 'book-to-skill-skill.js'), 'pdf2md', 'doctor'],
    { cwd: os.tmpdir(), encoding: 'utf8', env: { ...process.env, BOOK_TO_SKILL_PYTHON: '/nonexistent/python' } },
  );

  assert.equal(result.status, 1);
  assert.match(result.stderr, /no Python 3\.9\+ interpreter found/);
  assert.match(result.stderr, /BOOK_TO_SKILL_PYTHON/);
});

test('pdf2md propagates the Python CLI exit code', () => {
  const result = spawnSync(
    process.execPath,
    [path.join(packageRoot, 'bin', 'book-to-skill-skill.js'), 'pdf2md', 'benchmark', '--corpus', 'no-such-corpus.json', '--run-dir', 'unused'],
    { cwd: os.tmpdir(), encoding: 'utf8' },
  );

  assert.notEqual(result.status, 0);
});
