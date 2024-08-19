function apiBase(uri){
    return `/sdl-robot/api${uri}`
}
/**
 * 查询会话列表
 */
function getSessionList() {
    $.ajax({
        url: apiBase('/dh/session-list'),
        type: "GET",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                $('#dh-textarea').text(res.data);
                var scrollHeight = $('#dh-textarea').prop("scrollHeight");
                $('#dh-textarea').scrollTop(scrollHeight, 200);
            } else {
                toastr.error(res.message, '查询会话列表失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '查询会话列表失败');
        }
    });   
}
/**
 * 查询会话状态
 */
function getSessionStatus() {
    $.ajax({
        url: apiBase('/dh/session-status'),
        type: "GET",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                $('#dh-textarea').text(res.data);
                var scrollHeight = $('#dh-textarea').prop("scrollHeight");
                $('#dh-textarea').scrollTop(scrollHeight, 200);
            } else {
                toastr.error(res.message, '查询会话状态失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '查询会话状态失败');
        }
    });   
}
/**
 * 创建直播流会话
 */
function createSeesion() {
    $.ajax({
        url: apiBase('/dh/session-create'),
        type: "POST",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                $('#dh-textarea').text(res.data);
                var scrollHeight = $('#dh-textarea').prop("scrollHeight");
                $('#dh-textarea').scrollTop(scrollHeight, 200);
            } else {
                toastr.error(res.message, '创建会话失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '创建会话失败');
        }
    });   
}
/**
 * 开启会话
 */
function openSeesion() {
    $.ajax({
        url: apiBase('/dh/session-open'),
        type: "POST",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                $('#dh-textarea').text(res.data);
                var scrollHeight = $('#dh-textarea').prop("scrollHeight");
                $('#dh-textarea').scrollTop(scrollHeight, 200);
            } else {
                toastr.error(res.message, '开启会话失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '开启会话失败');
        }
    });   
}
/**
 * 关闭会话
 */
function closeSession() {
    $.ajax({
        url: apiBase('/dh/session-close'),
        type: "POST",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                $('#dh-textarea').text(res.data);
                var scrollHeight = $('#dh-textarea').prop("scrollHeight");
                $('#dh-textarea').scrollTop(scrollHeight, 200);
            } else {
                toastr.error(res.message, '关闭会话失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '关闭会话失败');
        }
    });   
}
/**
 * 创建指令长链接
 */
function createCmd() {
    $.ajax({
        url: apiBase('/dh/create-cmd'),
        type: "POST",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                $('#dh-textarea').text(res.data);
                var scrollHeight = $('#dh-textarea').prop("scrollHeight");
                $('#dh-textarea').scrollTop(scrollHeight, 200);
            } else {
                toastr.error(res.message, '创建长链接失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '创建长链接失败');
        }
    });   
}

$(function() {
    $('button#sessionList').on('click', function(e) {getSessionList();});
    $('button#sessionStatus').on('click', function(e) {getSessionStatus();});
    $('button#sessionCreate').on('click', function(e) {createSeesion();});
    $('button#sessionOpen').on('click', function(e) {openSeesion();});
    $('button#sessionClose').on('click', function(e) {closeSession();});
    $('button#cmdCreate').on('click', function(e) {createCmd();});
});

