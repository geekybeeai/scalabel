// Jest 26 cannot resolve the `node:` scheme; map node:stream here.
module.exports = require("stream")
