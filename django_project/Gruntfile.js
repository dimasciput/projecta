module.exports = function(grunt) {

    // Project configuration.
    grunt.initConfig({
        pkg: grunt.file.readJSON('package.json'),

        // Task configuration goes here.
        concat: {
            changes: {
                src: ['changes/static/js/*.js'],
                dest: '../static/js/changes.js'
            }
        },

        uglify: {
          changes: {
              files: {
                '../static/js/changes.min.js': ['changes/static/js/*.js']
              }
          }
        },

        requirejs: {
            compile: {
                options: {
                    baseUrl: 'todo/static/js',
                    mainConfigFile: 'todo/static/js/main.js',
                    name: 'modules/almond/almond',
                    include: ['main.js'],
                    out: 'todo/static/js/optimized.js'
                }
            }
        }

    });

    // Load plugins here.
    grunt.loadNpmTasks('grunt-contrib-concat');
    grunt.loadNpmTasks('grunt-contrib-uglify');
    grunt.loadNpmTasks('grunt-contrib-requirejs');

    // Register tasks here.
    grunt.registerTask('default', ['uglify']);
};
