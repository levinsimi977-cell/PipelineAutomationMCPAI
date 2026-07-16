//
//  AppDelegate.m
//  projectiv-c
//
//  Created by Test1 on 13/12/2023.
//

#import "AppDelegate.h"
#import "DLViewController.h"

@interface AppDelegate ()
@end

@implementation AppDelegate

@synthesize conversionData;

- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    return YES;
}

- (BOOL)application:(UIApplication *)application continueUserActivity:(NSUserActivity *)userActivity restorationHandler:(void (^)(NSArray * _Nullable))restorationHandler {
    return YES;
}

- (BOOL)application:(UIApplication *)app openURL:(NSURL *)url options:(NSDictionary<UIApplicationOpenURLOptionsKey,id> *)options {
    return YES;
}

- (void)application:(UIApplication *)application didReceiveRemoteNotification:(NSDictionary *)userInfo fetchCompletionHandler:(void (^)(UIBackgroundFetchResult))completionHandler {
    if (completionHandler) {
        completionHandler(UIBackgroundFetchResultNoData);
    }
}

- (void)walkToSceneWithParams:(NSString *)fruitName deepLinkData:(NSDictionary *)deepLinkData {
    UIStoryboard *storyboard = [UIStoryboard storyboardWithName:@"Main" bundle:nil];
    [[UIApplication sharedApplication].windows.firstObject.rootViewController dismissViewControllerAnimated:YES completion:nil];

    NSString *destVC = [fruitName stringByAppendingString:@"_vc"];
    DLViewController *newVC = [storyboard instantiateViewControllerWithIdentifier:destVC];

    NSLog(@"Routing to section: %@", destVC);
    newVC.deepLinkData = deepLinkData;

    [[UIApplication sharedApplication].windows.firstObject.rootViewController presentViewController:newVC animated:YES completion:nil];
}

@end
