const fs = require('fs');
const path = require('path');

const srcDir = path.join(__dirname, 'dist');
const destDir = path.join(__dirname, '..', '..', 'examples', 'web-viewer', 'dist');

function copyFolderSync(from, to) {
  if (!fs.existsSync(to)) {
    fs.mkdirSync(to, { recursive: true });
  }
  fs.readdirSync(from).forEach((element) => {
    const stat = fs.lstatSync(path.join(from, element));
    if (stat.isFile()) {
      fs.copyFileSync(path.join(from, element), path.join(to, element));
    } else if (stat.isDirectory()) {
      copyFolderSync(path.join(from, element), path.join(to, element));
    }
  });
}

try {
  if (fs.existsSync(srcDir)) {
    console.log(`Copying dist from ${srcDir} to ${destDir}...`);
    copyFolderSync(srcDir, destDir);
    console.log('Dist copied successfully!');
  } else {
    console.error('Source dist directory does not exist. Run npm run build first.');
    process.exit(1);
  }
} catch (error) {
  console.error('Error copying dist directory:', error);
  process.exit(1);
}
